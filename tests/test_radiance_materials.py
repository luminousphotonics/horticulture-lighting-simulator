from __future__ import annotations

import pytest

from fspm_optics.radiance.materials import (
    PRODUCTION_ROOM_FLOOR,
    PRODUCTION_ROOM_WALL_CEILING,
    RADIANCE_PLASTIC_MATERIALS,
    RadiancePlasticMaterial,
    get_plastic_material,
    validate_scalar_par_surface_material,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.fixtures.smd.radiance_writer import build_smd_radiance_document


def make_material(**overrides: object) -> RadiancePlasticMaterial:
    values: dict[str, object] = {
        "name": "test_white",
        "red_reflectance": 0.8,
        "green_reflectance": 0.7,
        "blue_reflectance": 0.6,
        "specularity": 0.1,
        "roughness": 0.2,
        "provenance": "unit test",
        "notes": "scalar test material",
    }
    values.update(overrides)
    return RadiancePlasticMaterial(**values)  # type: ignore[arg-type]


def test_valid_plastic_material_formats_as_radiance_text() -> None:
    assert make_material().to_radiance() == (
        "void plastic test_white\n"
        "0\n"
        "0\n"
        "5 0.8000 0.7000 0.6000 0.1000 0.2000\n"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("red_reflectance", -0.01),
        ("green_reflectance", 1.01),
        ("blue_reflectance", float("nan")),
        ("specularity", -0.1),
        ("roughness", 1.1),
    ],
)
def test_material_coefficients_must_be_unit_interval(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        make_material(**{field: value})


@pytest.mark.parametrize("name", ["", "white paint", "9white", "white/wall", "wall;bad"])
def test_material_names_must_be_radiance_safe(name: str) -> None:
    with pytest.raises(ValueError, match="material name"):
        make_material(name=name)


@pytest.mark.parametrize(
    ("material", "reflectance"),
    ((PRODUCTION_ROOM_WALL_CEILING, 0.90), (PRODUCTION_ROOM_FLOOR, 0.10)),
)
def test_production_room_materials_are_distinct_neutral_diffuse_authorities(
    material: RadiancePlasticMaterial, reflectance: float
) -> None:
    assert material.red_reflectance == reflectance
    assert material.green_reflectance == reflectance
    assert material.blue_reflectance == reflectance
    assert material.specularity == 0.0
    assert material.roughness == 0.0
    assert RADIANCE_PLASTIC_MATERIALS[material.name] is material
    assert get_plastic_material(material.name) is material
    assert validate_scalar_par_surface_material(material) is material


def test_production_room_materials_have_distinct_identifiers() -> None:
    assert PRODUCTION_ROOM_WALL_CEILING.name != PRODUCTION_ROOM_FLOOR.name


def test_scalar_par_surface_validation_rejects_colored_plastic() -> None:
    with pytest.raises(ValueError, match="must use equal RGB reflectance channels"):
        validate_scalar_par_surface_material(make_material())


def test_scalar_surface_validation_does_not_treat_smd_light_output_as_reflectance() -> None:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(layout, (1, 2, 3, 4, 5))
    emitter_document = build_smd_radiance_document(layout, schedule)
    assert " light smd_control_zone_" in emitter_document.radiance_text
    assert validate_scalar_par_surface_material(emitter_document) is emitter_document
