"""Strict source-neutral parser for the supported rectangular LM-63 subset."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
import re
from typing import Final

SUPPORTED_LM63_VERSIONS: Final = (
    "IESNA:LM-63-2002",
    "IESNA:LM-63-2019",
)
TYPE_C_PHOTOMETRY: Final = 1
IMPERIAL_UNITS: Final = 1
METRIC_UNITS: Final = 2
FEET_TO_METRES: Final = 0.3048
_FEET_TO_METRES_DECIMAL: Final = Decimal("0.3048")
_KEYWORD_RE = re.compile(r"^\[([^\]]+)\](.*)$")


class Lm63ParseError(ValueError):
    """LM-63 text is malformed or outside the shared supported subset."""


@dataclass(frozen=True, slots=True)
class Lm63Keyword:
    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name or any(character.isspace() for character in self.name):
            raise Lm63ParseError("LM-63 keyword names must be non-empty tokens.")


@dataclass(frozen=True, slots=True)
class Lm63Photometry:
    """Validated Type-C data with dimensions expressed deterministically in metres."""

    version: str
    keywords: tuple[Lm63Keyword, ...]
    tilt: str
    lamp_count: int
    lumens_per_lamp: float
    candela_multiplier: float
    vertical_angle_count: int
    horizontal_angle_count: int
    photometric_type: int
    units_type: int
    fixture_width_m: float
    fixture_length_m: float
    fixture_height_m: float
    source_fixture_width: float
    source_fixture_length: float
    source_fixture_height: float
    ballast_factor: float
    future_use_factor: float
    input_watts: float
    vertical_angles_deg: tuple[float, ...]
    horizontal_angles_deg: tuple[float, ...]
    candela_by_horizontal_plane: tuple[tuple[float, ...], ...]

    def keyword(self, name: str) -> str | None:
        wanted = name.upper()
        for keyword in self.keywords:
            if keyword.name.upper() == wanted:
                return keyword.value
        return None

    @property
    def candela_value_count(self) -> int:
        return self.vertical_angle_count * self.horizontal_angle_count

    @property
    def applicable_uniform_factor(self) -> float:
        return self.candela_multiplier * self.ballast_factor * self.future_use_factor

    @property
    def has_duplicate_horizontal_closure(self) -> bool:
        return (
            self.horizontal_angles_deg[0] == 0.0
            and self.horizontal_angles_deg[-1] == 360.0
            and self.candela_by_horizontal_plane[0]
            == self.candela_by_horizontal_plane[-1]
        )

    @property
    def is_quadrant_symmetric_type_c(self) -> bool:
        return (
            self.photometric_type == TYPE_C_PHOTOMETRY
            and self.horizontal_angles_deg[0] == 0.0
            and self.horizontal_angles_deg[-1] == 90.0
        )

    @property
    def is_downward_only(self) -> bool:
        return (
            self.vertical_angles_deg[0] == 0.0
            and self.vertical_angles_deg[-1] == 90.0
        )


def parse_lm63(text: str, *, source: str = "<memory>") -> Lm63Photometry:
    """Parse LM-63-2002/2019 Type-C rectangular photometry without expanding symmetry."""

    if not isinstance(text, str):
        raise TypeError("LM-63 input must be text.")
    lines = text.splitlines()
    if not lines:
        raise Lm63ParseError(f"LM-63 input is empty: {source}")
    version = lines[0].strip().lstrip("\ufeff")
    if version not in SUPPORTED_LM63_VERSIONS:
        raise Lm63ParseError(
            f"unsupported LM-63 version in {source}: {version!r}; supported versions are "
            f"{SUPPORTED_LM63_VERSIONS!r}."
        )

    keywords: list[Lm63Keyword] = []
    tilt_index: int | None = None
    tilt_value = ""
    for index, raw_line in enumerate(lines[1:], start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("TILT="):
            tilt_index = index
            tilt_value = line.partition("=")[2].strip()
            break
        match = _KEYWORD_RE.fullmatch(line)
        if match is None:
            raise Lm63ParseError(
                "LM-63 TILT declaration is missing before the numeric or malformed "
                f"header at line {index + 1} in {source}."
            )
        keywords.append(Lm63Keyword(match.group(1).strip(), match.group(2).strip()))
    if tilt_index is None:
        raise Lm63ParseError(f"LM-63 TILT declaration is missing: {source}")
    if tilt_value.upper() != "NONE":
        raise Lm63ParseError(
            f"unsupported LM-63 TILT={tilt_value!s} in {source}; only TILT=NONE is supported."
        )

    numeric_tokens = " ".join(lines[tilt_index + 1 :]).split()
    if len(numeric_tokens) < 13:
        raise Lm63ParseError(f"LM-63 numeric header is incomplete: {source}")
    values = [_finite_number(token, source=source) for token in numeric_tokens]
    lamp_count = _integer(values[0], "lamp count", source=source)
    vertical_count = _integer(values[3], "vertical angle count", source=source)
    horizontal_count = _integer(values[4], "horizontal angle count", source=source)
    photometric_type = _integer(values[5], "photometric type", source=source)
    units_type = _integer(values[6], "units type", source=source)

    if lamp_count <= 0:
        raise Lm63ParseError("LM-63 lamp count must be positive.")
    lumens_per_lamp = values[1]
    if lumens_per_lamp < 0.0 and lumens_per_lamp != -1.0:
        raise Lm63ParseError("LM-63 lumens per lamp must be non-negative or -1.")
    if values[2] <= 0.0:
        raise Lm63ParseError("LM-63 candela multiplier must be positive.")
    if vertical_count < 2 or horizontal_count < 2:
        raise Lm63ParseError("LM-63 angle counts must each be at least two.")
    if photometric_type != TYPE_C_PHOTOMETRY:
        raise Lm63ParseError("unsupported LM-63 photometric type; Type C (1) is required.")
    if units_type not in (IMPERIAL_UNITS, METRIC_UNITS):
        raise Lm63ParseError("unsupported LM-63 units; feet (1) or metres (2) are required.")
    source_dimensions = tuple(values[7:10])
    if source_dimensions[0] <= 0.0 or source_dimensions[1] <= 0.0:
        raise Lm63ParseError("LM-63 rectangular fixture width and length must be positive.")
    if source_dimensions[2] < 0.0:
        raise Lm63ParseError("LM-63 rectangular fixture height must be non-negative.")
    factors = values[10:12]
    if any(value <= 0.0 for value in factors):
        raise Lm63ParseError("LM-63 ballast and future-use factors must be positive.")
    if values[12] <= 0.0:
        raise Lm63ParseError("LM-63 input watts must be positive.")

    expected = 13 + vertical_count + horizontal_count + vertical_count * horizontal_count
    if len(values) != expected:
        relation = "missing" if len(values) < expected else "excess"
        raise Lm63ParseError(
            f"LM-63 has {relation} numeric values in {source}: "
            f"declared structure requires {expected}, found {len(values)}."
        )
    cursor = 13
    vertical_angles = tuple(values[cursor : cursor + vertical_count])
    cursor += vertical_count
    horizontal_angles = tuple(values[cursor : cursor + horizontal_count])
    cursor += horizontal_count
    candela_flat = tuple(values[cursor:])
    _validate_angles(vertical_angles, horizontal_angles)
    if any(value < 0.0 for value in candela_flat):
        raise Lm63ParseError("LM-63 candela values must be non-negative.")
    matrix = tuple(
        tuple(candela_flat[index : index + vertical_count])
        for index in range(0, len(candela_flat), vertical_count)
    )
    if horizontal_angles[-1] == 360.0 and matrix[0] != matrix[-1]:
        raise Lm63ParseError(
            "LM-63 360-degree closure plane must exactly duplicate the 0-degree plane."
        )
    dimensions_m = _dimensions_to_metres(
        numeric_tokens[7:10],
        units_type=units_type,
    )
    return Lm63Photometry(
        version=version,
        keywords=tuple(keywords),
        tilt="NONE",
        lamp_count=lamp_count,
        lumens_per_lamp=lumens_per_lamp,
        candela_multiplier=values[2],
        vertical_angle_count=vertical_count,
        horizontal_angle_count=horizontal_count,
        photometric_type=photometric_type,
        units_type=units_type,
        fixture_width_m=dimensions_m[0],
        fixture_length_m=dimensions_m[1],
        fixture_height_m=dimensions_m[2],
        source_fixture_width=source_dimensions[0],
        source_fixture_length=source_dimensions[1],
        source_fixture_height=source_dimensions[2],
        ballast_factor=factors[0],
        future_use_factor=factors[1],
        input_watts=values[12],
        vertical_angles_deg=vertical_angles,
        horizontal_angles_deg=horizontal_angles,
        candela_by_horizontal_plane=matrix,
    )


def _validate_angles(
    vertical: tuple[float, ...],
    horizontal: tuple[float, ...],
) -> None:
    if any(current <= previous for previous, current in zip(vertical, vertical[1:])):
        raise Lm63ParseError("LM-63 vertical angles must be strictly increasing.")
    if any(current <= previous for previous, current in zip(horizontal, horizontal[1:])):
        raise Lm63ParseError("LM-63 horizontal angles must be strictly increasing.")
    if vertical[0] != 0.0 or vertical[-1] not in (90.0, 180.0):
        raise Lm63ParseError(
            "supported Type-C LM-63 vertical angles must span 0-90 or 0-180 degrees."
        )
    if horizontal[0] != 0.0 or horizontal[-1] not in (90.0, 360.0):
        raise Lm63ParseError(
            "supported Type-C LM-63 horizontal angles must span 0-90 or 0-360 degrees."
        )


def _finite_number(token: str, *, source: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise Lm63ParseError(
            f"LM-63 numeric section contains a non-numeric token {token!r}: {source}"
        ) from exc
    if not math.isfinite(value):
        raise Lm63ParseError(
            f"LM-63 numeric section contains a non-finite value: {source}"
        )
    return value


def _dimensions_to_metres(
    source_tokens: list[str],
    *,
    units_type: int,
) -> tuple[float, float, float]:
    """Convert source dimension tokens without binary multiplication drift.

    LM-63 dimensions are decimal text. Imperial tokens are therefore multiplied
    by the exact decimal conversion factor 0.3048 before the final, unavoidable
    conversion to the public float representation. Metric tokens are converted
    directly from their decimal spelling. This makes equivalent source and
    derived metric documents resolve to the same canonical float values.
    """

    decimal_values = tuple(Decimal(token) for token in source_tokens)
    if units_type == IMPERIAL_UNITS:
        decimal_values = tuple(
            value * _FEET_TO_METRES_DECIMAL for value in decimal_values
        )
    return tuple(float(value) for value in decimal_values)


def _integer(value: float, label: str, *, source: str) -> int:
    if not value.is_integer():
        raise Lm63ParseError(f"LM-63 {label} must be an integer: {source}")
    return int(value)
