#!/usr/bin/env python3
"""Run inexpensive physical fixture-occlusion provenance and Radiance smokes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fspm_optics.fixtures.conventional_led import (  # noqa: E402
    CONVENTIONAL_SHARED_LIGHT_MODIFIER,
    build_conventional_radiance_source_plan,
    plan_conventional_layout_from_feet,
    validate_converted_ies_output,
)
from fspm_optics.fixtures.hps import (  # noqa: E402
    plan_hps_layout_from_feet,
)
from fspm_optics.fixtures.occlusion import (  # noqa: E402
    FIXTURE_BODY_MATERIAL_NAME,
    compile_fixture_occlusion,
    load_authenticated_fixture_asset,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
    validate_complete_classification_manifest,
)
from fspm_optics.fixtures.smd.positions import (  # noqa: E402
    generate_proposed_led_layout,
)
from fspm_optics.fixtures.smd.optical_stack import (  # noqa: E402
    APERTURE_SIDE_M,
    EMITTER_SIDE_M,
    EMITTER_Z_M,
    PMMA_SIDE_M,
    PMMA_TOP_Z_M,
)
from fspm_optics.radiance.commands import (  # noqa: E402
    CommandSpec,
    build_oconv_command,
)
from fspm_optics.radiance.runner import LocalRunner  # noqa: E402

FAR_FIELD_DISTANCE_M = 40.0
FAR_FIELD_ANGLES_DEG = (0, 15, 30, 45, 60, 75)
FAR_FIELD_RELATIVE_TOLERANCE = 0.01
ANGULAR_AZIMUTH_STEP_DEG = 15
ANGULAR_POLAR_RING_EDGES_DEG = (0, 15, 30, 45, 60, 75, 90)
STANDALONE_EXPECTED_BLOCKED_FRACTION = 1.0 / 9.0
STANDALONE_EXPECTED_FASTENER_NODE_PATHS = frozenset(
    {
        (
            "module_led_module/module_module_fasteners/"
            f"module_module_screw_{index:02d}"
        )
        for index in range(1, 5)
    }
)


def _require_success(result: object, label: str) -> None:
    if not getattr(result, "success", False):
        raise RuntimeError(
            getattr(result, "failure_message", None) or f"{label} failed"
        )


def _compile_body_scene(plan: object, root: Path, runner: LocalRunner) -> Path:
    materialize_fixture_occlusion(plan)
    compile_fixture_occlusion(plan, runner)
    octree = root / "fixture_bodies.oct"
    command = build_oconv_command(
        (plan.instance_source_path,),
        output_octree=octree,
        cwd=root,
        label=f"compile_{plan.system_id}_fixture_body_probe",
    )
    _require_success(runner.run(command), command.label)
    return octree


def _trace_modifiers(
    *,
    root: Path,
    octree: Path,
    rays: tuple[str, ...],
    label: str,
    runner: LocalRunner,
) -> tuple[str, ...]:
    input_path = root / f"{label}.rays"
    output_path = root / f"{label}.modifiers"
    input_path.write_text("\n".join(rays) + "\n", encoding="ascii")
    command = CommandSpec(
        argv=("rtrace", "-h", "-ab", "0", "-om", str(octree)),
        stdin_path=input_path,
        stdout_path=output_path,
        cwd=root,
        label=label,
    )
    _require_success(runner.run(command), label)
    return tuple(
        line.strip() for line in output_path.read_text(encoding="ascii").splitlines()
    )


def _trace_first_hits(
    *,
    root: Path,
    octree: Path,
    rays: tuple[str, ...],
    label: str,
    runner: LocalRunner,
) -> tuple[tuple[float, str, str], ...]:
    input_path = root / f"{label}.rays"
    output_path = root / f"{label}.first_hits.tsv"
    input_path.write_text("\n".join(rays) + "\n", encoding="ascii")
    command = CommandSpec(
        argv=("rtrace", "-h", "-ab", "0", "-oLsm", str(octree)),
        stdin_path=input_path,
        stdout_path=output_path,
        cwd=root,
        label=label,
    )
    _require_success(runner.run(command), label)
    output: list[tuple[float, str, str]] = []
    for line in output_path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise RuntimeError(f"{label} first-hit row is malformed")
        output.append((float(fields[0]), fields[1], fields[2]))
    if len(output) != len(rays):
        raise RuntimeError(f"{label} first-hit count changed")
    return tuple(output)


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


def _first_hit_node_path(
    *,
    surface: str,
    shape_id: str,
    asset_id: str,
) -> str | None:
    if surface == "*":
        return None
    prefix = shape_id.replace("-", "_")
    if not surface.startswith(prefix + "_t"):
        raise RuntimeError(f"cannot map fixture first-hit surface: {surface}")
    triangle_index = int(surface[len(prefix) + 2 :])
    matching = tuple(
        item
        for item in _primitive_triangle_ranges(asset_id)
        if item[0] <= triangle_index < item[1]
    )
    if len(matching) != 1:
        raise RuntimeError(f"cannot map fixture first-hit triangle: {surface}")
    return matching[0][4]


def _angular_fan_audit(
    *,
    root: Path,
    octree: Path,
    layout: object,
    plan: object,
    runner: LocalRunner,
) -> dict[str, object]:
    fixture_asset = {
        instance.fixture_id: instance.asset_id for instance in plan.instances
    }
    module_fixture: dict[int, object] = {}
    for fixture in layout.fixtures:
        for module_index in fixture.member_module_indices:
            if module_index in module_fixture:
                raise RuntimeError("Proposed module belongs to multiple fixtures")
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
    angular_samples: list[tuple[float, float, float, str]] = [
        (0.0, 0.0, 0.0, "diagnostic_exact_angle")
    ]
    azimuths = tuple(range(0, 360, ANGULAR_AZIMUTH_STEP_DEG))
    for polar in (15.0, 30.0, 45.0, 60.0, 75.0):
        angular_samples.extend(
            (polar, float(azimuth), 0.0, "diagnostic_exact_angle")
            for azimuth in azimuths
        )
    for lower, upper in zip(
        ANGULAR_POLAR_RING_EDGES_DEG[:-1],
        ANGULAR_POLAR_RING_EDGES_DEG[1:],
        strict=True,
    ):
        polar = (lower + upper) / 2.0
        ring_weight = (
            math.sin(math.radians(upper)) ** 2
            - math.sin(math.radians(lower)) ** 2
        )
        for azimuth in azimuths:
            angular_samples.append(
                (
                    polar,
                    float(azimuth),
                    ring_weight / len(azimuths),
                    "native_ppf_quadrature",
                )
            )

    rays: list[str] = []
    metadata: list[dict[str, object]] = []
    for module in layout.modules:
        fixture = module_fixture[module.module_index]
        target_asset_id = fixture_asset[fixture.fixture_id]
        for (
            position_name,
            offset_x,
            offset_y,
            position_weight,
        ) in aperture_positions:
            origin = (
                module.x_m + offset_x,
                module.y_m + offset_y,
                module.z_m,
            )
            for polar, azimuth, angular_weight, sample_role in angular_samples:
                polar_radians = math.radians(polar)
                azimuth_radians = math.radians(azimuth)
                direction = (
                    math.sin(polar_radians) * math.cos(azimuth_radians),
                    math.sin(polar_radians) * math.sin(azimuth_radians),
                    -math.cos(polar_radians),
                )
                rays.append(
                    " ".join(
                        f"{value:.17g}" for value in (*origin, *direction)
                    )
                )
                metadata.append(
                    {
                        "module_index": module.module_index,
                        "fixture_id": fixture.fixture_id,
                        "target_asset_id": target_asset_id,
                        "ring_index": module.control_zone_index,
                        "aperture_position": position_name,
                        "origin_m": list(origin),
                        "polar_deg": polar,
                        "azimuth_deg": azimuth,
                        "angular_sample_role": sample_role,
                        "direction": list(direction),
                        "native_downward_ppf_weight": (
                            angular_weight * position_weight
                        ),
                    }
                )
    hits = _trace_first_hits(
        root=root,
        octree=octree,
        rays=tuple(rays),
        label="proposed_native_angular_fan",
        runner=runner,
    )

    module_summary = {
        module.module_index: {"total": 0.0, "blocked": 0.0}
        for module in layout.modules
    }
    asset_summary = {
        asset_id: {"total": 0.0, "blocked": 0.0}
        for asset_id in sorted(set(fixture_asset.values()))
    }
    ring_summary = {
        module.control_zone_index: {"total": 0.0, "blocked": 0.0}
        for module in layout.modules
    }
    records: list[dict[str, object]] = []
    blocked_count = 0
    blocked_diagnostic_count = 0
    first_hit_node_summary: dict[str, dict[str, float]] = {}
    for base, (distance, surface, modifier) in zip(
        metadata, hits, strict=True
    ):
        blocked = surface != "*"
        hit_asset_id: str | None = None
        node_index: int | None = None
        primitive_index: int | None = None
        node_path: str | None = None
        if blocked:
            blocked_count += 1
            if base["angular_sample_role"] == "diagnostic_exact_angle":
                blocked_diagnostic_count += 1
            matching_shapes = tuple(
                (prefix, asset_id)
                for prefix, asset_id in shape_asset.items()
                if surface.startswith(prefix + "_t")
            )
            if len(matching_shapes) != 1:
                raise RuntimeError(
                    f"cannot map Proposed first-hit surface: {surface}"
                )
            prefix, hit_asset_id = matching_shapes[0]
            triangle_index = int(surface[len(prefix) + 2 :])
            matching_ranges = tuple(
                item
                for item in ranges[hit_asset_id]
                if item[0] <= triangle_index < item[1]
            )
            if len(matching_ranges) != 1:
                raise RuntimeError(
                    f"cannot map Proposed first-hit triangle: {surface}"
                )
            _, _, node_index, primitive_index, node_path = matching_ranges[0]
        weight = float(base["native_downward_ppf_weight"])
        module_bucket = module_summary[int(base["module_index"])]
        asset_bucket = asset_summary[str(base["target_asset_id"])]
        ring_bucket = ring_summary[int(base["ring_index"])]
        if weight > 0.0:
            for bucket in (module_bucket, asset_bucket, ring_bucket):
                bucket["total"] += weight
                if blocked:
                    bucket["blocked"] += weight
            if blocked and node_path is not None:
                node_bucket = first_hit_node_summary.setdefault(
                    node_path, {"blocked": 0.0}
                )
                node_bucket["blocked"] += weight
        records.append(
            base
            | {
                "blocked": blocked,
                "first_hit_distance_m": None if not blocked else distance,
                "first_hit_object": None if not blocked else surface,
                "first_hit_modifier": None if not blocked else modifier,
                "first_hit_asset_id": hit_asset_id,
                "first_hit_node_index": node_index,
                "first_hit_primitive_index": primitive_index,
                "first_hit_node_path": node_path,
            }
        )
    record_path = root / "proposed_native_angular_first_hits.jsonl"
    record_text = "".join(
        json.dumps(item, sort_keys=True) + "\n" for item in records
    )
    record_path.write_text(record_text, encoding="utf-8")

    def summary_payload(
        buckets: dict[object, dict[str, float]],
    ) -> dict[str, object]:
        return {
            str(key): {
                "sampled_native_downward_ppf_weight": value["total"],
                "blocked_native_downward_ppf_weight": value["blocked"],
                "blocked_fraction": value["blocked"] / value["total"],
            }
            for key, value in sorted(buckets.items())
        }

    total_weight = math.fsum(
        value["total"] for value in module_summary.values()
    )
    blocked_weight = math.fsum(
        value["blocked"] for value in module_summary.values()
    )
    return {
        "angular_model_id": "native_lambertian",
        "weighting": (
            "exact cos(theta)*sin(theta) integral per 15-degree polar ring; "
            "uniform aperture-area and 15-degree azimuth quadrature"
        ),
        "aperture_position_count": len(aperture_positions),
        "azimuth_step_deg": ANGULAR_AZIMUTH_STEP_DEG,
        "polar_ring_edges_deg": list(ANGULAR_POLAR_RING_EDGES_DEG),
        "maximum_sampled_polar_deg": max(
            item[0] for item in angular_samples
        ),
        "module_count": len(layout.modules),
        "ray_count": len(rays),
        "blocked_ray_count_including_zero_weight_axial_probes": blocked_count,
        "blocked_diagnostic_exact_angle_ray_count": (
            blocked_diagnostic_count
        ),
        "sampled_native_downward_ppf_weight": total_weight,
        "blocked_native_downward_ppf_weight": blocked_weight,
        "blocked_fraction": blocked_weight / total_weight,
        "by_module": summary_payload(module_summary),
        "by_asset": summary_payload(asset_summary),
        "by_ring": summary_payload(ring_summary),
        "blocked_weight_by_first_hit_node": {
            key: value["blocked"] / total_weight
            for key, value in sorted(first_hit_node_summary.items())
        },
        "records": {
            "path": str(record_path),
            "sha256": hashlib.sha256(
                record_text.encode("utf-8")
            ).hexdigest(),
            "count": len(records),
        },
    }


def _registration_artifacts(
    *,
    root: Path,
    asset_ids: tuple[str, ...],
) -> dict[str, object]:
    output_directory = root / "proposed-registration"
    output_directory.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, object] = {}
    for asset_id in asset_ids:
        authenticated = load_authenticated_fixture_asset(asset_id)
        registration = authenticated.proposed_aperture_registration
        if registration is None:
            raise RuntimeError(
                f"Proposed registration is missing: {asset_id}"
            )
        old_plane_y_mm = (
            math.fsum(item[1] for item in authenticated.asset.anchors_m)
            * 1000.0
            / len(authenticated.asset.anchors_m)
        )
        records: list[dict[str, object]] = []
        for item in authenticated.primitives:
            points = tuple(
                point
                for triangle in authenticated.decoded.triangles(
                    item.inventory
                )
                for point in triangle
            )
            local_min = tuple(
                min(point[axis] for point in points) for axis in range(3)
            )
            local_max = tuple(
                max(point[axis] for point in points) for axis in range(3)
            )
            records.append(
                {
                    "node_index": item.inventory.node_index,
                    "primitive_index": item.inventory.primitive_index,
                    "node_path": item.inventory.node_path,
                    "classification": item.classification,
                    "local_bounds_mm": [
                        list(local_min),
                        list(local_max),
                    ],
                    "pre_repair_scientific_vertical_bounds_relative_to_aperture_m": [
                        (old_plane_y_mm - local_max[1]) * 0.001,
                        (old_plane_y_mm - local_min[1]) * 0.001,
                    ],
                    "post_repair_scientific_vertical_bounds_relative_to_aperture_m": [
                        (
                            registration.local_plane_y_mm
                            - local_max[1]
                        )
                        * 0.001,
                        (
                            registration.local_plane_y_mm
                            - local_min[1]
                        )
                        * 0.001,
                    ],
                }
            )

        all_x = [
            value * 0.001
            for record in records
            for value in (
                record["local_bounds_mm"][0][0],
                record["local_bounds_mm"][1][0],
            )
        ]
        all_horizontal_z = [
            value * 0.001
            for record in records
            for value in (
                record["local_bounds_mm"][0][2],
                record["local_bounds_mm"][1][2],
            )
        ]
        side_z = [
            value
            for record in records
            for key in (
                "pre_repair_scientific_vertical_bounds_relative_to_aperture_m",
                "post_repair_scientific_vertical_bounds_relative_to_aperture_m",
            )
            for value in record[key]
        ]
        side_z.extend((0.0, PMMA_TOP_Z_M, EMITTER_Z_M))
        margin_x = max((max(all_x) - min(all_x)) * 0.04, 0.01)
        margin_z = max((max(side_z) - min(side_z)) * 0.08, 0.005)
        x_bounds = (min(all_x) - margin_x, max(all_x) + margin_x)
        side_bounds = (min(side_z) - margin_z, max(side_z) + margin_z)
        top_z_margin = max(
            (max(all_horizontal_z) - min(all_horizontal_z)) * 0.04,
            0.01,
        )
        top_z_bounds = (
            min(all_horizontal_z) - top_z_margin,
            max(all_horizontal_z) + top_z_margin,
        )
        panels = (
            (50.0, 70.0, 530.0, 260.0),
            (620.0, 70.0, 530.0, 260.0),
            (50.0, 415.0, 1100.0, 260.0),
        )

        def project(
            value: float,
            bounds: tuple[float, float],
            start: float,
            length: float,
            *,
            invert: bool = False,
        ) -> float:
            fraction = (value - bounds[0]) / (bounds[1] - bounds[0])
            if invert:
                fraction = 1.0 - fraction
            return start + fraction * length

        colors = {
            "external_occluder": "#30343b",
            "existing_optical_stack_duplicate": "#2878b5",
            "decorative_exclusion": "#a8adb5",
            "emitter": "#f39c12",
        }
        svg = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="730" '
            'viewBox="0 0 1200 730">',
            '<rect width="1200" height="730" fill="white"/>',
            f'<text x="50" y="30" font-family="sans-serif" font-size="20">'
            f"{asset_id} source/body registration</text>",
            '<text x="50" y="51" font-family="monospace" font-size="12">'
            f"GLB plane correction = "
            f"{registration.local_plane_y_mm - old_plane_y_mm:.6f} mm; "
            "blue=excluded cover, dark=physical occluder, "
            "green=calibrated optical stack</text>",
        ]
        for panel, title in zip(
            panels[:2],
            ("Pre-repair side overlay", "Corrected side overlay"),
            strict=True,
        ):
            x, y, width, height = panel
            svg.extend(
                (
                    f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
                    'fill="#fafafa" stroke="#777"/>',
                    f'<text x="{x}" y="{y - 12}" font-family="sans-serif" '
                    f'font-size="15">{title}</text>',
                )
            )
        for record in records:
            local = record["local_bounds_mm"]
            x0 = local[0][0] * 0.001
            x1 = local[1][0] * 0.001
            for panel_index, key in enumerate(
                (
                    "pre_repair_scientific_vertical_bounds_relative_to_aperture_m",
                    "post_repair_scientific_vertical_bounds_relative_to_aperture_m",
                )
            ):
                panel = panels[panel_index]
                z0, z1 = record[key]
                px0 = project(x0, x_bounds, panel[0], panel[2])
                px1 = project(x1, x_bounds, panel[0], panel[2])
                py0 = project(
                    z1, side_bounds, panel[1], panel[3], invert=True
                )
                py1 = project(
                    z0, side_bounds, panel[1], panel[3], invert=True
                )
                svg.append(
                    f'<rect x="{px0:.3f}" y="{py0:.3f}" '
                    f'width="{max(px1 - px0, 0.25):.3f}" '
                    f'height="{max(py1 - py0, 0.25):.3f}" '
                    f'fill="{colors[record["classification"]]}" '
                    'fill-opacity="0.10" stroke-opacity="0.22" '
                    f'stroke="{colors[record["classification"]]}" '
                    'stroke-width="0.35"/>'
                )
        for panel in panels[:2]:
            aperture_y = project(
                0.0, side_bounds, panel[1], panel[3], invert=True
            )
            svg.append(
                f'<line x1="{panel[0]}" y1="{aperture_y:.3f}" '
                f'x2="{panel[0] + panel[2]}" y2="{aperture_y:.3f}" '
                'stroke="#d62728" stroke-width="1.5"/>'
            )
            for anchor in authenticated.asset.anchors_m:
                center = anchor[0]
                source_x0 = project(
                    center - PMMA_SIDE_M / 2.0,
                    x_bounds,
                    panel[0],
                    panel[2],
                )
                source_x1 = project(
                    center + PMMA_SIDE_M / 2.0,
                    x_bounds,
                    panel[0],
                    panel[2],
                )
                source_y0 = project(
                    EMITTER_Z_M,
                    side_bounds,
                    panel[1],
                    panel[3],
                    invert=True,
                )
                source_y1 = project(
                    0.0,
                    side_bounds,
                    panel[1],
                    panel[3],
                    invert=True,
                )
                svg.append(
                    f'<rect x="{source_x0:.3f}" y="{source_y0:.3f}" '
                    f'width="{source_x1 - source_x0:.3f}" '
                    f'height="{source_y1 - source_y0:.3f}" '
                    'fill="#2ca02c" fill-opacity="0.16" '
                    'stroke="#168a16" stroke-width="0.8"/>'
                )

        top = panels[2]
        svg.extend(
            (
                f'<rect x="{top[0]}" y="{top[1]}" width="{top[2]}" '
                f'height="{top[3]}" fill="#fafafa" stroke="#777"/>',
                f'<text x="{top[0]}" y="{top[1] - 12}" '
                'font-family="sans-serif" font-size="15">'
                "Corrected top orthographic bounds and 126 mm apertures</text>",
            )
        )
        for record in records:
            local = record["local_bounds_mm"]
            x0, x1 = local[0][0] * 0.001, local[1][0] * 0.001
            z0, z1 = local[0][2] * 0.001, local[1][2] * 0.001
            px0 = project(x0, x_bounds, top[0], top[2])
            px1 = project(x1, x_bounds, top[0], top[2])
            py0 = project(
                z1, top_z_bounds, top[1], top[3], invert=True
            )
            py1 = project(
                z0, top_z_bounds, top[1], top[3], invert=True
            )
            svg.append(
                f'<rect x="{px0:.3f}" y="{py0:.3f}" '
                f'width="{max(px1 - px0, 0.25):.3f}" '
                f'height="{max(py1 - py0, 0.25):.3f}" '
                f'fill="{colors[record["classification"]]}" '
                'fill-opacity="0.06" stroke-opacity="0.18" '
                f'stroke="{colors[record["classification"]]}" '
                'stroke-width="0.3"/>'
            )
        for anchor in authenticated.asset.anchors_m:
            px0 = project(
                anchor[0] - APERTURE_SIDE_M / 2.0,
                x_bounds,
                top[0],
                top[2],
            )
            px1 = project(
                anchor[0] + APERTURE_SIDE_M / 2.0,
                x_bounds,
                top[0],
                top[2],
            )
            py0 = project(
                anchor[2] + APERTURE_SIDE_M / 2.0,
                top_z_bounds,
                top[1],
                top[3],
                invert=True,
            )
            py1 = project(
                anchor[2] - APERTURE_SIDE_M / 2.0,
                top_z_bounds,
                top[1],
                top[3],
                invert=True,
            )
            svg.append(
                f'<rect x="{px0:.3f}" y="{py0:.3f}" '
                f'width="{px1 - px0:.3f}" height="{py1 - py0:.3f}" '
                'fill="#2ca02c" fill-opacity="0.10" '
                'stroke="#168a16" stroke-width="1"/>'
            )
        svg.append("</svg>\n")
        svg_text = "\n".join(svg)
        svg_path = output_directory / f"{asset_id}.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        report = {
            "asset_id": asset_id,
            "old_module_anchor_plane_local_y_mm": old_plane_y_mm,
            "corrected_aperture_plane_local_y_mm": (
                registration.local_plane_y_mm
            ),
            "derived_vertical_correction_mm": (
                registration.local_plane_y_mm - old_plane_y_mm
            ),
            "registration": registration.to_payload(),
            "scientific_optical_stack_relative_to_aperture_m": {
                "aperture_plane_z": 0.0,
                "aperture_side": APERTURE_SIDE_M,
                "pmma_top_z": PMMA_TOP_Z_M,
                "pmma_side": PMMA_SIDE_M,
                "internal_emitter_z": EMITTER_Z_M,
                "internal_emitter_side": EMITTER_SIDE_M,
            },
            "primitives": records,
        }
        report_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        report_path = output_directory / f"{asset_id}.json"
        report_path.write_text(report_text, encoding="utf-8")
        artifacts[asset_id] = {
            "svg_path": str(svg_path),
            "svg_sha256": hashlib.sha256(
                svg_text.encode("utf-8")
            ).hexdigest(),
            "registration_report_path": str(report_path),
            "registration_report_sha256": hashlib.sha256(
                report_text.encode("utf-8")
            ).hexdigest(),
            "old_module_anchor_plane_local_y_mm": old_plane_y_mm,
            "corrected_aperture_plane_local_y_mm": (
                registration.local_plane_y_mm
            ),
            "derived_vertical_correction_mm": (
                registration.local_plane_y_mm - old_plane_y_mm
            ),
        }
    return artifacts


def _aperture_polygons(apertures: tuple[object, ...]) -> str:
    lines: list[str] = []
    for aperture in apertures:
        lines.extend(
            (
                f"{CONVENTIONAL_SHARED_LIGHT_MODIFIER} polygon "
                f"{aperture.polygon_id}",
                "0",
                "0",
                "12",
                *(
                    " ".join(f"{value:.17g}" for value in vertex)
                    for vertex in aperture.vertices_m
                ),
            )
        )
    return "\n".join(lines) + "\n"


def _trace_irradiance(
    *,
    root: Path,
    source_name: str,
    source_text: str,
    receiver_text: str,
    runner: LocalRunner,
) -> tuple[float, ...]:
    source_path = root / f"{source_name}.rad"
    octree_path = root / f"{source_name}.oct"
    receiver_path = root / "far_field.rays"
    output_path = root / f"{source_name}.rgb"
    source_path.write_text(source_text, encoding="ascii")
    receiver_path.write_text(receiver_text, encoding="ascii")
    compile_command = build_oconv_command(
        (source_path,),
        output_octree=octree_path,
        cwd=root,
        label=f"compile_{source_name}",
    )
    _require_success(runner.run(compile_command), compile_command.label)
    trace_command = CommandSpec(
        argv=(
            "rtrace",
            "-h",
            "-I+",
            "-ab",
            "0",
            "-dt",
            "0",
            str(octree_path),
        ),
        stdin_path=receiver_path,
        stdout_path=output_path,
        cwd=root,
        label=f"trace_{source_name}",
    )
    _require_success(runner.run(trace_command), trace_command.label)
    values = tuple(
        float(line.split()[0])
        for line in output_path.read_text(encoding="ascii").splitlines()
    )
    if len(values) != len(FAR_FIELD_ANGLES_DEG) or any(
        not math.isfinite(value) or value <= 0.0 for value in values
    ):
        raise RuntimeError(f"{source_name} far-field output is invalid")
    return values


def run_audit(root: Path) -> dict[str, object]:
    runner = LocalRunner()
    classifications = validate_complete_classification_manifest()
    registration_artifacts = _registration_artifacts(
        root=root,
        asset_ids=tuple(
            item.asset.asset_id
            for item in classifications
            if item.asset.system_id == "proposed"
        ),
    )

    conventional_layout = plan_conventional_layout_from_feet(10, 10)
    conventional = plan_fixture_occlusion(
        system_id="conventional",
        layout_identity=conventional_layout.to_payload(),
        output_directory=root / "conventional" / "fixture_occlusion",
    )
    conventional_octree = _compile_body_scene(
        conventional,
        root / "conventional",
        runner,
    )
    source = build_conventional_radiance_source_plan(
        conventional_layout,
        workspace=root / "conventional" / "photometry",
        emitting_boundaries=conventional.emitting_boundaries,
    )
    source.ies2rad.expected_paths.workspace.mkdir(parents=True, exist_ok=True)
    source.ies2rad.expected_paths.derived_ies.write_text(
        source.derived_ies.text,
        encoding="ascii",
    )
    _require_success(
        runner.run(source.ies2rad.command),
        source.ies2rad.command.label,
    )
    raw_rad = source.ies2rad.expected_paths.converted_rad.read_text(
        encoding="ascii"
    )
    bar_contract = validate_converted_ies_output(
        raw_rad,
        emitting_boundary_area_m2=(
            source.emitting_boundary_area_m2_per_fixture
        ),
    )
    footprint_contract = validate_converted_ies_output(raw_rad)

    first_bars = source.apertures[:8]
    first_bar = first_bars[0]
    second_bar = first_bars[1]
    bar_center = tuple(
        math.fsum(vertex[axis] for vertex in first_bar.vertices_m) / 4.0
        for axis in range(2)
    )
    opening_center = (
        (
            max(vertex[0] for vertex in first_bar.vertices_m)
            + min(vertex[0] for vertex in second_bar.vertices_m)
        )
        / 2.0,
        bar_center[1],
    )
    instance = conventional.instances[0]
    shape = conventional.shapes[0]
    frame_point = (
        instance.translation_m[0] + shape.bounds_m[0][0] + 0.001,
        bar_center[1],
    )
    aperture_z = first_bar.vertices_m[0][2]
    conventional_hits = _trace_modifiers(
        root=root / "conventional",
        octree=conventional_octree,
        rays=(
            f"{bar_center[0]:.17g} {bar_center[1]:.17g} "
            f"{aperture_z - 0.001:.17g} 0 0 1",
            f"{opening_center[0]:.17g} {opening_center[1]:.17g} "
            f"{aperture_z - 0.001:.17g} 0 0 1",
            f"{frame_point[0]:.17g} {frame_point[1]:.17g} "
            f"{aperture_z - 0.001:.17g} 0 0 1",
            f"{bar_center[0]:.17g} {bar_center[1]:.17g} "
            f"{aperture_z - 0.001:.17g} 0 0 -1",
        ),
        label="conventional_visibility",
        runner=runner,
    )
    if conventional_hits != (
        FIXTURE_BODY_MATERIAL_NAME,
        "*",
        FIXTURE_BODY_MATERIAL_NAME,
        "*",
    ):
        raise RuntimeError(
            f"Conventional bar/opening ray probes changed: {conventional_hits}"
        )

    xs = [vertex[0] for item in first_bars for vertex in item.vertices_m]
    ys = [vertex[1] for item in first_bars for vertex in item.vertices_m]
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    receiver_rows: list[str] = []
    for angle in FAR_FIELD_ANGLES_DEG:
        radians = math.radians(angle)
        receiver_rows.append(
            " ".join(
                f"{value:.17g}"
                for value in (
                    center_x + FAR_FIELD_DISTANCE_M * math.sin(radians),
                    center_y,
                    aperture_z
                    - FAR_FIELD_DISTANCE_M * math.cos(radians),
                    -math.sin(radians),
                    0.0,
                    math.cos(radians),
                )
            )
        )
    receiver_text = "\n".join(receiver_rows) + "\n"
    bar_values = _trace_irradiance(
        root=root / "conventional" / "photometry",
        source_name="eight_glb_apertures",
        source_text=(
            bar_contract.shared_definition_text
            + "\n"
            + _aperture_polygons(first_bars)
        ),
        receiver_text=receiver_text,
        runner=runner,
    )
    half_x = 1.190 / 2.0
    half_y = 1.087 / 2.0
    footprint = type(
        "Footprint",
        (),
        {
            "polygon_id": "diagnostic_full_footprint",
            "vertices_m": (
                (center_x - half_x, center_y + half_y, aperture_z),
                (center_x + half_x, center_y + half_y, aperture_z),
                (center_x + half_x, center_y - half_y, aperture_z),
                (center_x - half_x, center_y - half_y, aperture_z),
            ),
        },
    )()
    footprint_values = _trace_irradiance(
        root=root / "conventional" / "photometry",
        source_name="diagnostic_authenticated_ies_footprint",
        source_text=(
            footprint_contract.shared_definition_text
            + "\n"
            + _aperture_polygons((footprint,))
        ),
        receiver_text=receiver_text,
        runner=runner,
    )
    relative_errors = tuple(
        abs(actual - reference) / reference
        for actual, reference in zip(
            bar_values,
            footprint_values,
            strict=True,
        )
    )
    if max(relative_errors) > FAR_FIELD_RELATIVE_TOLERANCE:
        raise RuntimeError(
            "eight-bar aggregate far field exceeds authenticated IES tolerance"
        )

    proposed_layout = generate_proposed_led_layout(10, 10)
    proposed = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(proposed_layout),
        output_directory=root / "proposed" / "fixture_occlusion",
    )
    proposed_octree = _compile_body_scene(
        proposed,
        root / "proposed",
        runner,
    )
    proposed_hits = _trace_modifiers(
        root=root / "proposed",
        octree=proposed_octree,
        rays=tuple(
            f"{module.x_m:.17g} {module.y_m:.17g} "
            f"{module.z_m:.17g} 0 0 -1"
            for module in proposed_layout.modules
        )
        + (
            f"-0.35 -0.35 "
            f"{proposed_layout.modules[0].z_m - 0.01:.17g} 0 0 1",
        ),
        label="proposed_visibility",
        runner=runner,
    )
    if (
        any(hit != "*" for hit in proposed_hits[:-1])
        or proposed_hits[-1] != FIXTURE_BODY_MATERIAL_NAME
    ):
        raise RuntimeError("Proposed direct-source/support ray probes changed")
    angular = _angular_fan_audit(
        root=root / "proposed",
        octree=proposed_octree,
        layout=proposed_layout,
        plan=proposed,
        runner=runner,
    )
    blocked_fraction = float(angular["blocked_fraction"])
    blocked_nodes = angular["blocked_weight_by_first_hit_node"]
    if (
        not math.isclose(
            blocked_fraction,
            STANDALONE_EXPECTED_BLOCKED_FRACTION,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        or set(blocked_nodes) != STANDALONE_EXPECTED_FASTENER_NODE_PATHS
        or any(
            not math.isclose(
                float(value),
                1.0 / 36.0,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
            for value in blocked_nodes.values()
        )
    ):
        raise RuntimeError(
            "standalone Proposed native angular blockage no longer matches "
            "the four authenticated corner fasteners"
        )

    hps_layout = plan_hps_layout_from_feet(10, 10)
    hps = plan_fixture_occlusion(
        system_id="hps",
        layout_identity=hps_layout.to_payload(),
        output_directory=root / "hps" / "fixture_occlusion",
    )
    hps_octree = _compile_body_scene(
        hps,
        root / "hps",
        runner,
    )
    hps_instance = hps.instances[0]
    hps_shape = hps.shapes[0]

    def hps_ray(
        origin_cad_mm: tuple[float, float, float],
        direction_cad: tuple[float, float, float],
    ) -> str:
        origin = (
            hps_instance.translation_m[0] + origin_cad_mm[0] * 0.001,
            hps_instance.translation_m[1] - origin_cad_mm[1] * 0.001,
            hps_instance.translation_m[2] + origin_cad_mm[2] * 0.001,
        )
        direction = (
            direction_cad[0],
            -direction_cad[1],
            direction_cad[2],
        )
        return " ".join(f"{value:.17g}" for value in (*origin, *direction))

    luminous_origin = (0.0, 0.0, -138.0)
    hps_hits = _trace_first_hits(
        root=root / "hps",
        octree=hps_octree,
        rays=(
            hps_ray(
                luminous_origin,
                (-0.9978678013211558, 0.0, -0.06526753470510753),
            ),
            hps_ray(
                (-249.68758200989214, 0.0, -218.4230406325425),
                (-0.9304494532673857, 0.0, 0.3664202708836165),
            ),
            hps_ray(luminous_origin, (0.0, 0.0, 1.0)),
            hps_ray(luminous_origin, (0.0, 0.0, -1.0)),
        ),
        label="hps_authenticated_body_visibility",
        runner=runner,
    )
    hps_hit_nodes = tuple(
        _first_hit_node_path(
            surface=surface,
            shape_id=hps_shape.shape_id,
            asset_id=hps_shape.asset_id,
        )
        for _, surface, _ in hps_hits
    )
    if (
        hps_hits[0][2] != FIXTURE_BODY_MATERIAL_NAME
        or hps_hit_nodes[0]
        not in {
            "competitor_hps_1000w/socket_bracket_assembly/socket_bracket",
            "competitor_hps_1000w/socket_bracket_assembly/ceramic_socket",
            "competitor_hps_1000w/hps_bulb_base",
        }
        or hps_hits[1][1:] != ("*", "*")
        or hps_hits[2][2] != FIXTURE_BODY_MATERIAL_NAME
        or hps_hit_nodes[2]
        != "competitor_hps_1000w/reflector_hood/reflector_hood_top_box"
        or hps_hits[3][1:] != ("*", "*")
    ):
        raise RuntimeError(
            f"HPS body visibility probes changed: {hps_hits}"
        )

    return {
        "classification_assets": {
            item.asset.asset_id: item.classification_counts
            for item in classifications
        },
        "proposed_registration_artifacts": registration_artifacts,
        "conventional": {
            "fixture_count": len(conventional.instances),
            "apertures_per_fixture": 8,
            "emitting_boundary_area_m2_per_fixture": (
                source.emitting_boundary_area_m2_per_fixture
            ),
            "projected_opaque_area_m2": (
                conventional.total_projected_opaque_area_m2
            ),
            "external_triangle_instances": (
                conventional.total_external_triangle_instances
            ),
            "visibility_hits": list(conventional_hits),
            "far_field_angles_deg": list(FAR_FIELD_ANGLES_DEG),
            "far_field_relative_errors": list(relative_errors),
            "far_field_max_relative_error": max(relative_errors),
            "far_field_tolerance": FAR_FIELD_RELATIVE_TOLERANCE,
        },
        "proposed": {
            "fixture_count": len(proposed.instances),
            "module_direct_rays_unblocked": len(proposed_hits) - 1,
            "support_probe_hit": proposed_hits[-1],
            "native_angular_emission": angular,
            "projected_opaque_area_m2": (
                proposed.total_projected_opaque_area_m2
            ),
            "external_triangle_instances": (
                proposed.total_external_triangle_instances
            ),
        },
        "hps": {
            "fixture_count": len(hps.instances),
            "unique_body_shapes": len(hps.shapes),
            "body_primitives_per_shape": hps_shape.primitive_count,
            "body_triangles_per_shape": hps_shape.triangle_count,
            "projected_opaque_area_m2_per_fixture": (
                hps_shape.projected_opaque_area_m2
            ),
            "external_triangle_instances": (
                hps.total_external_triangle_instances
            ),
            "emitting_boundaries": len(hps.emitting_boundaries),
            "visibility_hits": [
                {
                    "distance_m": distance,
                    "surface": surface,
                    "modifier": modifier,
                    "node_path": node_path,
                }
                for (distance, surface, modifier), node_path in zip(
                    hps_hits, hps_hit_nodes, strict=True
                )
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        help="optional absent or empty directory in which to retain artifacts",
    )
    arguments = parser.parse_args()
    if arguments.workspace is None:
        with tempfile.TemporaryDirectory(
            prefix="fspm-fixture-occlusion-audit-"
        ) as temporary:
            payload = run_audit(Path(temporary))
    else:
        workspace = arguments.workspace.expanduser().resolve()
        if workspace.exists() and (
            not workspace.is_dir() or any(workspace.iterdir())
        ):
            raise SystemExit("audit workspace must be absent or empty")
        workspace.mkdir(parents=True, exist_ok=True)
        payload = run_audit(workspace)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
