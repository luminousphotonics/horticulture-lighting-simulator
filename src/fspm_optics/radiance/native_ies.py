"""Strict, source-neutral validation for limited native ``ies2rad`` output."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


class NativeIesOutputError(ValueError):
    """Native ``ies2rad`` output violates the explicitly supported subset."""


@dataclass(frozen=True, slots=True)
class RadiancePrimitive:
    modifier: str
    primitive_type: str
    identifier: str
    string_arguments: tuple[str, ...]
    integer_arguments: tuple[int, ...]
    real_arguments: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class NativeFlatcorrOutputContract:
    angular_modifier_id: str
    light_modifier_id: str
    dat_reference: str
    cal_reference: str
    aperture_brightdata_scale: float
    length_x_m: float
    width_y_m: float
    native_polygon_z_m: float
    neutral_rgb: tuple[float, float, float]
    primitive_count: int
    geometry_ids: tuple[str, ...]
    shared_definition_text: str


def validate_native_zero_height_flatcorr_output(
    text: str,
    *,
    output_root: str,
    angular_modifier_id: str,
    light_modifier_id: str,
    dat_reference: str,
    carrier_multiplier: float,
    length_x_m: float,
    width_y_m: float,
    native_polygon_z_m: float = -0.00025,
) -> NativeFlatcorrOutputContract:
    """Validate the one-polygon zero-height Type-C output emitted by ``ies2rad``.

    Radiance represents an LM-63 zero-height rectangle as one downward polygon
    at its fixed 0.25 mm native guard offset.  That converted polygon is a
    validation input only; callers reuse the validated angular/light definition
    with their own aperture polygons at the modeled aperture plane.
    """

    for name, value in (
        ("carrier_multiplier", carrier_multiplier),
        ("length_x_m", length_x_m),
        ("width_y_m", width_y_m),
        ("native_polygon_z_m", native_polygon_z_m),
    ):
        if not math.isfinite(float(value)):
            raise NativeIesOutputError(f"{name} must be finite.")
    if not output_root or not angular_modifier_id or not light_modifier_id:
        raise NativeIesOutputError("native source identifiers must be non-empty.")
    if not dat_reference or length_x_m <= 0.0 or width_y_m <= 0.0:
        raise NativeIesOutputError("native DAT reference and aperture dimensions are required.")

    primitives = parse_radiance_primitives(text)
    if len(primitives) != 3:
        raise NativeIesOutputError(
            "expected one brightdata, one light, and one downward flat aperture."
        )
    brightdata, light, polygon = primitives
    if (
        brightdata.modifier != "void"
        or brightdata.primitive_type != "brightdata"
        or brightdata.identifier != angular_modifier_id
    ):
        raise NativeIesOutputError("native angular brightdata identity is invalid.")
    if brightdata.string_arguments != (
        "flatcorr",
        dat_reference,
        "source.cal",
        "src_phi",
        "src_theta",
    ):
        raise NativeIesOutputError("brightdata references unexpected DAT/CAL functions.")
    expected_scale = carrier_multiplier / (length_x_m * width_y_m)
    if (
        brightdata.integer_arguments
        or len(brightdata.real_arguments) != 1
        or not math.isclose(
            brightdata.real_arguments[0],
            expected_scale,
            rel_tol=1e-6,
            abs_tol=1e-9,
        )
    ):
        raise NativeIesOutputError("flatcorr carrier or aperture-area scale is invalid.")
    if (
        light.modifier != angular_modifier_id
        or light.primitive_type != "light"
        or light.identifier != light_modifier_id
        or light.string_arguments
        or light.integer_arguments
        or light.real_arguments != (1.0, 1.0, 1.0)
    ):
        raise NativeIesOutputError("expected one neutral equal-channel light modifier.")
    _validate_downward_rectangle(
        polygon,
        modifier=light_modifier_id,
        identifier=f"{output_root}.d",
        length_x_m=length_x_m,
        width_y_m=width_y_m,
        z_m=native_polygon_z_m,
    )
    shared_text = format_radiance_primitive(brightdata) + "\n" + format_radiance_primitive(light)
    return NativeFlatcorrOutputContract(
        angular_modifier_id=angular_modifier_id,
        light_modifier_id=light_modifier_id,
        dat_reference=dat_reference,
        cal_reference="source.cal",
        aperture_brightdata_scale=brightdata.real_arguments[0],
        length_x_m=length_x_m,
        width_y_m=width_y_m,
        native_polygon_z_m=native_polygon_z_m,
        neutral_rgb=(1.0, 1.0, 1.0),
        primitive_count=3,
        geometry_ids=(polygon.identifier,),
        shared_definition_text=shared_text,
    )


def parse_radiance_primitives(text: str) -> tuple[RadiancePrimitive, ...]:
    tokens: list[str] = []
    for line in text.splitlines():
        content = line.partition("#")[0].strip()
        if content:
            tokens.extend(
                _unquote(token)
                for token in re.findall(r"'[^']*'|\"[^\"]*\"|\S+", content)
            )
    primitives: list[RadiancePrimitive] = []
    cursor = 0
    try:
        while cursor < len(tokens):
            modifier, primitive_type, identifier = tokens[cursor : cursor + 3]
            cursor += 3
            string_count = int(tokens[cursor])
            cursor += 1
            if string_count < 0:
                raise ValueError
            strings = tuple(tokens[cursor : cursor + string_count])
            cursor += string_count
            integer_count = int(tokens[cursor])
            cursor += 1
            if integer_count < 0:
                raise ValueError
            integers = tuple(int(value) for value in tokens[cursor : cursor + integer_count])
            cursor += integer_count
            real_count = int(tokens[cursor])
            cursor += 1
            if real_count < 0:
                raise ValueError
            reals = tuple(float(value) for value in tokens[cursor : cursor + real_count])
            cursor += real_count
            if any(not math.isfinite(value) for value in reals):
                raise ValueError
            primitives.append(
                RadiancePrimitive(
                    modifier,
                    primitive_type,
                    identifier,
                    strings,
                    integers,
                    reals,
                )
            )
    except (IndexError, ValueError) as exc:
        raise NativeIesOutputError("malformed limited ies2rad RAD output.") from exc
    if not primitives:
        raise NativeIesOutputError("converted RAD output contains no primitives.")
    return tuple(primitives)


def format_radiance_primitive(primitive: RadiancePrimitive) -> str:
    strings = " ".join(primitive.string_arguments)
    integers = " ".join(str(value) for value in primitive.integer_arguments)
    reals = " ".join(_number(value) for value in primitive.real_arguments)
    return (
        f"{primitive.modifier} {primitive.primitive_type} {primitive.identifier}\n"
        f"{len(primitive.string_arguments)}{(' ' + strings) if strings else ''}\n"
        f"{len(primitive.integer_arguments)}{(' ' + integers) if integers else ''}\n"
        f"{len(primitive.real_arguments)}{(' ' + reals) if reals else ''}\n"
    )


def _validate_downward_rectangle(
    polygon: RadiancePrimitive,
    *,
    modifier: str,
    identifier: str,
    length_x_m: float,
    width_y_m: float,
    z_m: float,
) -> None:
    if (
        polygon.modifier != modifier
        or polygon.primitive_type != "polygon"
        or polygon.identifier != identifier
        or polygon.string_arguments
        or polygon.integer_arguments
        or len(polygon.real_arguments) != 12
    ):
        raise NativeIesOutputError("native output contains unexpected source geometry.")
    vertices = tuple(
        tuple(polygon.real_arguments[index : index + 3])
        for index in range(0, 12, 3)
    )
    expected = (
        (-length_x_m / 2.0, -width_y_m / 2.0, z_m),
        (-length_x_m / 2.0, width_y_m / 2.0, z_m),
        (length_x_m / 2.0, width_y_m / 2.0, z_m),
        (length_x_m / 2.0, -width_y_m / 2.0, z_m),
    )
    for actual, wanted in zip(vertices, expected, strict=True):
        if any(
            not math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)
            for a, b in zip(actual, wanted, strict=True)
        ):
            raise NativeIesOutputError(
                "native flat aperture is not the expected centered length-X/width-Y rectangle."
            )
    if _polygon_unit_normal(vertices) != (0.0, 0.0, -1.0):
        raise NativeIesOutputError("native flat aperture is not aimed toward negative Z.")


def _polygon_unit_normal(
    vertices: tuple[tuple[float, float, float], ...],
) -> tuple[float, float, float]:
    a, b, c = vertices[:3]
    first = tuple(b[index] - a[index] for index in range(3))
    second = tuple(c[index] - a[index] for index in range(3))
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    magnitude = math.sqrt(sum(value * value for value in cross))
    if magnitude == 0.0:
        raise NativeIesOutputError("native aperture polygon is degenerate.")
    return tuple(
        0.0 if abs(value / magnitude) < 1e-15 else value / magnitude
        for value in cross
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _number(value: float | int) -> str:
    return format(float(value), ".15g")
