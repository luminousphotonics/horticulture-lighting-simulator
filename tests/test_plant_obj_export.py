from __future__ import annotations

import json
from pathlib import Path

from fspm_optics import cli
from fspm_optics.plants import (
    DEFAULT_REX_OBJ_MATERIAL_NAME,
    RexPlantConfig,
    export_plant_mesh_to_obj,
    export_rex_material_to_mtl,
    generate_rex_butterhead_plant,
    rex_geometry_summary,
    write_plant_mesh_obj,
)


def compact_plant():
    return generate_rex_butterhead_plant(
        RexPlantConfig(
            leaf_count=24,
            leaf_patch_u=2,
            leaf_patch_v=2,
        )
    )


def test_obj_debug_export_preserves_vertex_and_face_counts() -> None:
    plant = compact_plant()
    text = export_plant_mesh_to_obj(plant)

    assert sum(line.startswith("v ") for line in text.splitlines()) == (
        plant.vertex_count
    )
    assert sum(line.startswith("f ") for line in text.splitlines()) == plant.face_count
    assert sum(line.startswith("g ") for line in text.splitlines()) == len(
        plant.leaves
    )
    assert "mtllib rex_butterhead.mtl" in text
    assert f"usemtl {DEFAULT_REX_OBJ_MATERIAL_NAME}" in text
    assert text == export_plant_mesh_to_obj(plant)


def test_default_mtl_contains_visual_lettuce_material() -> None:
    text = export_rex_material_to_mtl()

    assert f"newmtl {DEFAULT_REX_OBJ_MATERIAL_NAME}" in text
    assert "Kd 0.180000 0.480000 0.145000" in text
    assert "not an optical transport definition" in text


def test_obj_writer_is_a_thin_wrapper_over_the_scientific_mesh(
    tmp_path: Path,
) -> None:
    plant = compact_plant()
    path = tmp_path / "rex_debug.obj"
    material_path = path.with_suffix(".mtl")

    assert write_plant_mesh_obj(path, plant) == path
    assert material_path.is_file()
    assert path.read_text(encoding="utf-8") == export_plant_mesh_to_obj(
        plant,
        material_library_filename=material_path.name,
    )
    assert material_path.read_text(encoding="utf-8") == (
        export_rex_material_to_mtl()
    )


def test_rex_export_cli_writes_obj_and_matching_json_summary(
    tmp_path: Path,
    capsys,
) -> None:
    output = tmp_path / "rex_butterhead.obj"
    exit_code = cli.main(
        [
            "rex-plant-export",
            "--output",
            str(output),
            "--seed",
            "7",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    summary_path = output.with_suffix(".json")
    material_path = output.with_suffix(".mtl")
    expected = rex_geometry_summary(
        generate_rex_butterhead_plant(RexPlantConfig(seed=7))
    )
    assert exit_code == 0
    assert output.is_file()
    assert material_path.is_file()
    assert summary_path.is_file()
    assert payload["obj_path"] == str(output.resolve())
    assert payload["mtl_path"] == str(material_path.resolve())
    assert payload["summary_path"] == str(summary_path.resolve())
    assert payload["summary"] == expected
    assert json.loads(summary_path.read_text(encoding="utf-8")) == expected
    assert payload["summary"]["leaf_count"] == 32
    assert payload["summary"]["patch_count"] == 512
    assert payload["summary"]["receiver_count"] == 1024
    assert payload["summary"]["leaf_patch_grid"] == [4, 4]
    assert f"mtllib {material_path.name}" in output.read_text(encoding="utf-8")
    assert f"newmtl {DEFAULT_REX_OBJ_MATERIAL_NAME}" in material_path.read_text(
        encoding="utf-8"
    )
