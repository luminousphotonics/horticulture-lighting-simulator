from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_CONVERTED_ANGULAR_MODIFIER,
    CONVENTIONAL_CONVERTED_LIGHT_MODIFIER,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    CONVENTIONAL_SHARED_ANGULAR_MODIFIER,
    CONVENTIONAL_SHARED_LIGHT_MODIFIER,
    RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W,
    ConvertedIesOutputError,
    build_conventional_radiance_source_plan,
    build_ies2rad_command_plan,
    derive_conventional_carrier_scale,
    plan_conventional_layout_from_feet,
    validate_converted_ies_output,
)


def _converted_rad() -> str:
    lx = 1.190 / 2.0
    wy = 1.087 / 2.0
    hz = 0.108 / 2.0
    corners = {
        0: (-lx, -wy, -hz),
        1: (lx, -wy, -hz),
        2: (-lx, wy, -hz),
        3: (lx, wy, -hz),
        4: (-lx, -wy, hz),
        5: (lx, -wy, hz),
        6: (-lx, wy, hz),
        7: (lx, wy, hz),
    }
    faces = (
        (".d", (0, 2, 3, 1)),
        (".u", (4, 5, 7, 6)),
        (".1", (0, 1, 5, 4)),
        (".2", (1, 3, 7, 5)),
        (".3", (3, 2, 6, 7)),
        (".4", (2, 0, 4, 6)),
    )
    sections = [
        "void brightdata conventional_led_unit_downward_flux_dist\n"
        "5 boxcorr conventional_led_unit_downward_flux.dat source.cal src_phi src_theta\n"
        "0\n"
        "4 307164 1.190 1.087 0.108\n",
        "conventional_led_unit_downward_flux_dist light "
        "conventional_led_unit_downward_flux_light\n"
        "0\n0\n3 1 1 1\n",
    ]
    for suffix, indices in faces:
        values = " ".join(
            str(value) for index in indices for value in corners[index]
        )
        sections.append(
            "conventional_led_unit_downward_flux_light polygon "
            f"conventional_led_unit_downward_flux{suffix}\n0\n0\n12 {values}\n"
        )
    return "\n".join(sections)


def test_carrier_scale_derivation_compensates_exactly_once_for_ies2rad_white_efficacy() -> None:
    scale = derive_conventional_carrier_scale()

    assert RADIANCE_UNIFORM_WHITE_LUMINOUS_EFFICACY_LM_PER_W == 179.0
    assert CONVENTIONAL_FIXTURE_PPF_UMOL_S == 1716.0
    assert scale.ies2rad_multiplier == 307164.0
    assert scale.ies2rad_multiplier / 179.0 == 1716.0
    assert scale.to_payload()["post_trace_179_conversion"] is False


def test_ies2rad_command_is_deterministic_neutral_and_non_executing() -> None:
    workspace = Path("runtime workspace with spaces")
    plan = build_ies2rad_command_plan(
        workspace,
        derived_ies_filename="conventional_led_unit_downward_flux.ies",
        carrier_scale=derive_conventional_carrier_scale(),
        ies2rad_bin=workspace / "bin with spaces" / "ies2rad",
    )

    assert plan.command.argv == (
        str(workspace / "bin with spaces" / "ies2rad"),
        "-dm",
        "-t",
        "default",
        "-c",
        "1",
        "1",
        "1",
        "-m",
        "307164",
        "-o",
        "conventional_led_unit_downward_flux",
        "conventional_led_unit_downward_flux.ies",
    )
    assert plan.command.cwd == workspace
    assert plan.command.stdin_path is None
    assert plan.command.stdout_path is None
    assert plan.command.env == {}
    assert plan.command.argv[0] == str(workspace / "bin with spaces" / "ies2rad")
    assert len(plan.command.argv) == 13
    assert all(not token.startswith("!") for token in plan.command.argv)
    assert plan.scientific_payload()["shell"] is False
    assert workspace.parts[-1] == "runtime workspace with spaces"
    assert not workspace.exists()


def test_strict_converted_output_accepts_centered_length_x_width_y_downward_box() -> None:
    contract = validate_converted_ies_output(_converted_rad())

    assert contract.converted_angular_modifier_id == (
        CONVENTIONAL_CONVERTED_ANGULAR_MODIFIER
    )
    assert contract.converted_light_modifier_id == CONVENTIONAL_CONVERTED_LIGHT_MODIFIER
    assert contract.clean_angular_modifier_id == CONVENTIONAL_SHARED_ANGULAR_MODIFIER
    assert contract.clean_light_modifier_id == CONVENTIONAL_SHARED_LIGHT_MODIFIER
    assert "unit_downward_flux" in contract.clean_angular_modifier_id
    assert "unit_downward_flux" in contract.clean_light_modifier_id
    assert contract.dat_reference == "conventional_led_unit_downward_flux.dat"
    assert contract.cal_reference == "source.cal"
    assert contract.length_x_m == 1.190
    assert contract.width_y_m == 1.087
    assert contract.height_z_m == 0.108
    assert contract.clean_aperture_brightdata_scale == pytest.approx(
        307164.0 / (1.190 * 1.087)
    )
    assert contract.neutral_rgb == (1.0, 1.0, 1.0)
    assert contract.primitive_count == 8
    assert "flatcorr" in contract.shared_definition_text
    assert "boxcorr" not in contract.shared_definition_text
    assert CONVENTIONAL_SHARED_LIGHT_MODIFIER in contract.shared_definition_text


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("3 1 1 1", "3 1 0.9 1", "neutral"),
        ("source.cal", "unknown.cal", "DAT/CAL"),
        ("4 307164", "4 1716", "carrier"),
        ("conventional_led_unit_downward_flux.d", "unexpected.d", "DAT/CAL"),
    ],
)
def test_strict_converted_output_rejects_unknown_scale_resources_or_geometry(
    old: str, new: str, message: str
) -> None:
    with pytest.raises(ConvertedIesOutputError, match=message):
        validate_converted_ies_output(_converted_rad().replace(old, new, 1))


def test_strict_converted_output_rejects_extra_emitter() -> None:
    extra = (
        "\nvoid light unexpected_light\n0\n0\n3 1 1 1\n"
        "unexpected_light polygon unexpected_polygon\n0\n0\n12 "
        "0 0 0 0 1 0 1 1 0 1 0 0\n"
    )
    with pytest.raises(ConvertedIesOutputError, match="one brightdata"):
        validate_converted_ies_output(_converted_rad() + extra)


def test_strict_converted_output_rejects_non_downward_lower_winding() -> None:
    original = "-0.595 -0.5435 -0.054 -0.595 0.5435 -0.054"
    reversed_start = "-0.595 0.5435 -0.054 -0.595 -0.5435 -0.054"
    with pytest.raises(ConvertedIesOutputError, match="negative Z"):
        validate_converted_ies_output(
            _converted_rad().replace(original, reversed_start, 1)
        )


def test_source_plan_has_eight_downward_glb_bar_apertures_per_fixture() -> None:
    layout = plan_conventional_layout_from_feet(10, 10)
    plan = build_conventional_radiance_source_plan(layout, workspace="runtime")

    assert len(layout.fixtures) == 4
    assert len(plan.apertures) == len(layout.fixtures) * 8 == 32
    assert len({item.source_id for item in plan.apertures}) == 32
    assert len({item.polygon_id for item in plan.apertures}) == 32
    assert all(item.normal == (0.0, 0.0, -1.0) for item in plan.apertures)
    assert all(
        item.angular_light_modifier_id == CONVENTIONAL_SHARED_LIGHT_MODIFIER
        for item in plan.apertures
    )
    assert all(len(item.vertices_m) == 4 for item in plan.apertures)
    for item in plan.apertures:
        xs = [vertex[0] for vertex in item.vertices_m]
        ys = [vertex[1] for vertex in item.vertices_m]
        zs = {vertex[2] for vertex in item.vertices_m}
        assert min(max(xs) - min(xs), max(ys) - min(ys)) < 0.023
        assert max(max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx(
            1.013056398262612,
        )
        assert len(zs) == 1
        assert item.transported_downward_ppf_umol_s == pytest.approx(
            1716.0 / 8.0
        )
    assert plan.emitting_boundary_area_m2_per_fixture == pytest.approx(
        0.17822199169532674
    )
    assert plan.emitting_boundary_area_m2_per_fixture < 1.190 * 1.087
    text = plan.aperture_radiance_text()
    assert text.count(" polygon ") == 32
    assert "!xform" not in text
    assert "full_footprint" not in text.lower()


def test_rolling_bench_export_uses_authoritative_simulation_coordinates() -> None:
    layout = plan_conventional_layout_from_feet(
        24,
        12,
        policy="rolling_bench",
    )
    source = build_conventional_radiance_source_plan(
        layout,
        workspace="runtime",
    )

    assert [item.fixture_id for item in source.apertures[::8]] == [
        item.identity.fixture_id for item in layout.fixtures
    ]
    for fixture in layout.fixtures:
        apertures = tuple(
            item
            for item in source.apertures
            if item.fixture_id == fixture.identity.fixture_id
        )
        assert tuple(item.bar_index for item in apertures) == tuple(range(8))
        xs = [vertex[0] for item in apertures for vertex in item.vertices_m]
        ys = [vertex[1] for item in apertures for vertex in item.vertices_m]
        assert (min(xs) + max(xs)) / 2.0 == pytest.approx(
            fixture.aperture_center_m.aligned_x_m
        )
        assert (min(ys) + max(ys)) / 2.0 == pytest.approx(
            fixture.aperture_center_m.aligned_y_m
        )
        assert max(xs) - min(xs) == pytest.approx(1.013056398262612)
        assert max(ys) - min(ys) == pytest.approx(0.9840035402551424)


def test_source_order_and_ppf_invariants_follow_layout_without_eightfold_duplication() -> None:
    layout = plan_conventional_layout_from_feet(16, 8)
    plan = build_conventional_radiance_source_plan(layout, workspace="runtime")

    assert [(item.row_y, item.column_x) for item in plan.apertures] == sorted(
        (item.row_y, item.column_x) for item in plan.apertures
    )
    assert plan.transported_downward_ppf_umol_s_per_fixture == 1716.0
    assert plan.whole_layout_transported_downward_ppf_umol_s == (
        len(layout.fixtures) * 1716.0
    )
    assert len(plan.apertures) == len(layout.fixtures) * 8
    assert all(
        item.transported_downward_ppf_umol_s == pytest.approx(1716.0 / 8.0)
        for item in plan.apertures
    )
    assert sum(
        item.transported_downward_ppf_umol_s for item in plan.apertures
    ) == pytest.approx(len(layout.fixtures) * 1716.0)
    assert plan.whole_layout_transported_downward_ppf_umol_s != (
        len(layout.fixtures) * 8 * 1716.0
    )
    assert plan.scientific_payload()["post_trace_scale"] is None
    assert plan.original_ies.tested_input_watts == pytest.approx(663.20)
    assert plan.original_ies.future_use_factor_2019 == pytest.approx(1.1)
    assert plan.derived_ies.to_payload()["neutralized_lm63_fields"] == {
        "candela_multiplier": 1.0,
        "ballast_factor": 1.0,
        "future_use_factor": 1.0,
        "input_watts": 1.0,
    }
    assert plan.derived_ies.derived_flux.downward_hemisphere_flux_cd_sr == (
        pytest.approx(1.0, abs=1e-12)
    )
    assert plan.derived_ies.original_flux.upward_fraction_of_full_sphere > 0.0
    assert plan.scientific_payload()["transport_approximation"] == {
        "geometry": "eight_glb_derived_downward_bar_apertures_per_fixture",
        "photometric_boundary": "illum",
        "transported_hemisphere": "type_c_vertical_0_to_90_degrees",
        "converted_full_box_enters_final_scene": False,
        "upward_or_lateral_faces_enter_final_scene": False,
        "downward_carrier_closure_is_exact_by_construction": True,
    }


def test_source_identity_is_path_independent_and_distinct_from_smd() -> None:
    layout = plan_conventional_layout_from_feet(10, 10)
    first = build_conventional_radiance_source_plan(
        layout, workspace=Path("first runtime")
    )
    second = build_conventional_radiance_source_plan(
        layout, workspace=Path("another/runtime")
    )

    assert first.source_plan_id == second.source_plan_id
    assert first.scientific_payload() == second.scientific_payload()
    assert first.to_json() != second.to_json()
    assert first.source_plan_id.startswith("conventional-radiance-source-v3-")
    assert "smd" not in first.source_plan_id
    assert "/home/austin" not in first.source_plan_id
    repeat = build_conventional_radiance_source_plan(
        layout, workspace=Path("first runtime")
    )
    assert first.to_json() == repeat.to_json()


def test_source_adapter_imports_no_runner_or_process_execution() -> None:
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "fixtures"
        / "conventional_led"
        / "radiance_source.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "fspm_optics.radiance.runner" not in imported_modules
    assert "subprocess" not in source
    assert "LocalRunner" not in source
