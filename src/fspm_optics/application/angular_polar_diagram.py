"""Deterministic presentation artifacts for authoritative angular profiles."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from typing import Final, Mapping, Sequence

from fspm_optics.fixtures.smd.source_variants import (
    NARROW_SMD_SOURCE_VARIANT,
    NATIVE_SMD_SOURCE_VARIANT,
    WIDE_SMD_SOURCE_VARIANT,
    measured_fwhm_deg,
    measured_optical_axis_reference_fwhm_deg,
    profile_sha256,
)
from fspm_optics.transport.basis.atomic import atomic_write_bytes

POLAR_SVG_FILENAME: Final = "angular-polar-comparison.svg"
POLAR_PNG_FILENAME: Final = "angular-polar-comparison.png"
POLAR_RAW_SVG_FILENAME: Final = "angular-polar-comparison-raw.svg"
POLAR_RAW_PNG_FILENAME: Final = "angular-polar-comparison-raw.png"
POLAR_SVG_WIDTH: Final = 1920
POLAR_SVG_HEIGHT: Final = 1080
POLAR_PNG_WIDTH: Final = 3840
POLAR_PNG_HEIGHT: Final = 2160
POLAR_DIAGRAM_SCHEMA_ID: Final = "fspm-optics.angular-polar-comparison"
POLAR_DIAGRAM_SCHEMA_VERSION: Final = 1
POLAR_REVALIDATED_DIAGRAM_SCHEMA_VERSION: Final = 2
NORMALIZED_RADIAL_TICKS: Final = (0.25, 0.5, 0.75, 1.0)
INKSCAPE_INCOMPATIBLE_SNAP_ENVIRONMENT_VARIABLES: Final = (
    "GTK_PATH",
    "GTK_PATH_VSCODE_SNAP_ORIG",
    "GIO_MODULE_DIR",
    "GIO_MODULE_DIR_VSCODE_SNAP_ORIG",
)
_GRID_COLOR: Final = "#d2d2d2"
_CURVE_COLOR: Final = "#111111"
_FONT_STACK: Final = (
    "'STIX Two Text', 'Times New Roman', 'DejaVu Serif', serif"
)


class AngularPolarDiagramError(RuntimeError):
    """The polar comparison could not be authenticated or rendered."""


@dataclass(frozen=True, slots=True)
class _PanelSpec:
    key: str
    title: str
    label: str
    center_x: float
    center_y: float


_PANELS: Final = (
    _PanelSpec(
        "conventional",
        "Conventional LED",
        "(a)",
        480.0,
        160.0,
    ),
    _PanelSpec(
        NATIVE_SMD_SOURCE_VARIANT,
        "Modularized System",
        "(b)",
        480.0,
        650.0,
    ),
    _PanelSpec(
        WIDE_SMD_SOURCE_VARIANT,
        "Modularized-Altered (140° FWHM)",
        "(c)",
        1440.0,
        160.0,
    ),
    _PanelSpec(
        NARROW_SMD_SOURCE_VARIANT,
        "Modularized-Altered (100° FWHM)",
        "(d)",
        1440.0,
        650.0,
    ),
)
_PANEL_RADIUS: Final = 185.0


def build_angular_polar_comparison_svg(
    profiles: Sequence[Mapping[str, object]],
    *,
    footer_text: str | None = None,
    fwhm_label_decimals: int = 3,
    scientific_data_transform: bool = False,
) -> bytes:
    """Build the authoritative 2×2 vector figure from exact profile arrays."""

    validated = _validated_profiles(profiles)
    provenance = {
        "schema_id": POLAR_DIAGRAM_SCHEMA_ID,
        "schema_version": POLAR_DIAGRAM_SCHEMA_VERSION,
        "quantity": "normalized_completed_aperture_radiant_intensity",
        "scientific_data_transform": scientific_data_transform,
        "radial_scale": {
            "minimum": 0.0,
            "maximum": 1.0,
            "ticks": list(NORMALIZED_RADIAL_TICKS),
            "identical_in_all_panels": True,
        },
        "angular_convention": {
            "optical_axis_deg": 0.0,
            "optical_axis_page_direction": "down",
            "left_horizon_deg": -90.0,
            "right_horizon_deg": 90.0,
            "profiles_mirrored_about_optical_axis": True,
        },
        "panels": [
            {
                "label": panel.label,
                "profile_key": panel.key,
                "display_title": panel.title,
                "profile_sha256": validated[panel.key]["profile_sha256"],
                "characterization_identity_sha256": validated[panel.key].get(
                    "characterization_identity_sha256"
                ),
                "source_authority": validated[panel.key]["source_authority"],
                "curve_element_id": f"curve-{panel.key}",
            }
            for panel in _PANELS
        ],
    }
    metadata = escape(
        json.dumps(
            provenance,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        quote=False,
    )
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {POLAR_SVG_WIDTH} {POLAR_SVG_HEIGHT}" '
            f'width="{POLAR_SVG_WIDTH}" height="{POLAR_SVG_HEIGHT}" '
            'role="img" aria-labelledby="figure-title figure-description">'
        ),
        (
            "<title id=\"figure-title\">Completed-aperture angular-profile "
            "comparison</title>"
        ),
        (
            "<desc id=\"figure-description\">Four normalized polar profiles "
            "on one common zero-to-one radial scale, with zero degrees on the "
            "downward optical axis.</desc>"
        ),
        f'<metadata id="angular-polar-provenance">{metadata}</metadata>',
        '<rect width="1920" height="1080" fill="#ffffff"/>',
        (
            '<line x1="960" y1="42" x2="960" y2="1018" '
            f'stroke="{_GRID_COLOR}" stroke-width="1"/>'
        ),
        (
            '<line x1="54" y1="540" x2="1866" y2="540" '
            f'stroke="{_GRID_COLOR}" stroke-width="1"/>'
        ),
    ]
    for panel in _PANELS:
        parts.extend(
            _panel_svg(
                panel,
                validated[panel.key],
                fwhm_label_decimals=fwhm_label_decimals,
            )
        )
    footer = footer_text or (
        "Normalized completed-aperture radiant intensity · common radial "
        "scale 0–1 · 0° downward, ±90° horizon"
    )
    parts.extend(
        (
            (
                '<text x="960" y="1042" text-anchor="middle" '
                f'font-family="{_FONT_STACK}" font-size="20" fill="#333333">'
                f"{escape(footer)}</text>"
            ),
            "</svg>",
        )
    )
    return ("\n".join(parts) + "\n").encode("utf-8")


def render_angular_polar_comparison_png(svg_bytes: bytes) -> bytes:
    """Rasterize the exact vector artifact at the project's 4K slide size."""

    if not svg_bytes.startswith(b"<?xml") or b"<svg" not in svg_bytes:
        raise AngularPolarDiagramError("polar SVG input is malformed.")
    renderer = shutil.which("inkscape")
    if renderer is None:
        raise AngularPolarDiagramError(
            "Inkscape is required to rasterize the authoritative SVG to 4K PNG."
        )
    with tempfile.TemporaryDirectory(prefix="fspm-angular-polar-") as temporary:
        root = Path(temporary)
        svg_path = root / POLAR_SVG_FILENAME
        png_path = root / POLAR_PNG_FILENAME
        atomic_write_bytes(svg_path, svg_bytes)
        environment = dict(os.environ)
        for variable in INKSCAPE_INCOMPATIBLE_SNAP_ENVIRONMENT_VARIABLES:
            environment.pop(variable, None)
        environment["XDG_CONFIG_HOME"] = str(root / "config")
        completed = subprocess.run(
            (
                renderer,
                "--export-type=png",
                f"--export-filename={png_path}",
                f"--export-width={POLAR_PNG_WIDTH}",
                f"--export-height={POLAR_PNG_HEIGHT}",
                str(svg_path),
            ),
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120.0,
        )
        if completed.returncode != 0 or not png_path.is_file():
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AngularPolarDiagramError(
                "Inkscape polar-diagram rasterization failed"
                + (f": {stderr}" if stderr else ".")
            )
        png_bytes = png_path.read_bytes()
    if _png_dimensions(png_bytes) != (POLAR_PNG_WIDTH, POLAR_PNG_HEIGHT):
        raise AngularPolarDiagramError(
            "polar PNG does not have the required 3840×2160 dimensions."
        )
    return png_bytes


def publish_angular_polar_comparison(
    output_directory: str | Path,
    profiles: Sequence[Mapping[str, object]],
    *,
    require_existing: bool = False,
) -> dict[str, object]:
    """Publish or authenticate the deterministic SVG and PNG pair."""

    root = Path(output_directory)
    svg_path = root / POLAR_SVG_FILENAME
    png_path = root / POLAR_PNG_FILENAME
    svg_bytes = build_angular_polar_comparison_svg(profiles)
    png_bytes = render_angular_polar_comparison_png(svg_bytes)
    existing = (svg_path.exists(), png_path.exists())
    if require_existing and existing != (True, True):
        raise AngularPolarDiagramError(
            "completed summary requires both polar diagram artifacts to exist."
        )
    if existing not in {(False, False), (True, True)}:
        raise AngularPolarDiagramError(
            "polar diagram artifact pair is partial and cannot be repaired safely."
        )
    if existing == (True, True):
        if (
            not svg_path.is_file()
            or not png_path.is_file()
            or svg_path.read_bytes() != svg_bytes
            or png_path.read_bytes() != png_bytes
        ):
            raise AngularPolarDiagramError(
                "existing polar diagram artifacts conflict with authoritative profiles."
            )
    else:
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(svg_path, svg_bytes)
        atomic_write_bytes(png_path, png_bytes)
    declaration = _diagram_declaration(profiles, svg_bytes, png_bytes)
    authenticate_angular_polar_comparison(root, declaration)
    return declaration


def publish_revalidated_angular_polar_comparison(
    output_directory: str | Path,
    *,
    presentation_profiles: Sequence[Mapping[str, object]],
    raw_profiles: Sequence[Mapping[str, object]],
    transformation: Mapping[str, object],
    curve_provenance: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Republish clean and raw figures from authenticated validation arrays."""

    root = Path(output_directory)
    clean_svg = build_angular_polar_comparison_svg(
        presentation_profiles,
        footer_text=(
            "Curves use a 2° centered angular-bin mean · authenticated raw "
            "profiles are archived"
        ),
        fwhm_label_decimals=1,
        scientific_data_transform=True,
    )
    raw_svg = build_angular_polar_comparison_svg(
        raw_profiles,
        footer_text=(
            "Authenticated raw completed-aperture validation profiles · "
            "0° downward, ±90° horizon"
        ),
        fwhm_label_decimals=1,
        scientific_data_transform=False,
    )
    clean_png = render_angular_polar_comparison_png(clean_svg)
    raw_png = render_angular_polar_comparison_png(raw_svg)
    root.mkdir(parents=True, exist_ok=True)
    artifacts = (
        (POLAR_SVG_FILENAME, clean_svg),
        (POLAR_PNG_FILENAME, clean_png),
        (POLAR_RAW_SVG_FILENAME, raw_svg),
        (POLAR_RAW_PNG_FILENAME, raw_png),
    )
    for filename, data in artifacts:
        path = root / filename
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise AngularPolarDiagramError(
                f"polar diagram republish target is unsafe: {filename}."
            )
    for filename, data in artifacts:
        atomic_write_bytes(root / filename, data)
    declaration = _revalidated_diagram_declaration(
        presentation_profiles=presentation_profiles,
        raw_profiles=raw_profiles,
        transformation=transformation,
        curve_provenance=curve_provenance,
        clean_svg=clean_svg,
        clean_png=clean_png,
        raw_svg=raw_svg,
        raw_png=raw_png,
    )
    authenticate_angular_polar_comparison(root, declaration)
    return declaration


def authenticate_angular_polar_comparison(
    output_directory: str | Path,
    declaration: Mapping[str, object],
) -> None:
    """Fail closed unless a summary declaration matches both exact artifacts."""

    root = Path(output_directory).resolve()
    schema_version = declaration.get("schema_version")
    if (
        declaration.get("schema_id") != POLAR_DIAGRAM_SCHEMA_ID
        or schema_version
        not in {
            POLAR_DIAGRAM_SCHEMA_VERSION,
            POLAR_REVALIDATED_DIAGRAM_SCHEMA_VERSION,
        }
    ):
        raise AngularPolarDiagramError("polar diagram declaration is incompatible.")
    artifacts = declaration.get("artifacts")
    legacy_expected = {
        "authoritative_vector": (
            POLAR_SVG_FILENAME,
            "image/svg+xml",
            POLAR_SVG_WIDTH,
            POLAR_SVG_HEIGHT,
        ),
        "high_resolution_raster": (
            POLAR_PNG_FILENAME,
            "image/png",
            POLAR_PNG_WIDTH,
            POLAR_PNG_HEIGHT,
        ),
    }
    revalidated_expected = {
        **legacy_expected,
        "raw_audit_vector": (
            POLAR_RAW_SVG_FILENAME,
            "image/svg+xml",
            POLAR_SVG_WIDTH,
            POLAR_SVG_HEIGHT,
        ),
        "raw_audit_raster": (
            POLAR_RAW_PNG_FILENAME,
            "image/png",
            POLAR_PNG_WIDTH,
            POLAR_PNG_HEIGHT,
        ),
    }
    expected = (
        legacy_expected
        if schema_version == POLAR_DIAGRAM_SCHEMA_VERSION
        else revalidated_expected
    )
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(expected):
        raise AngularPolarDiagramError(
            "polar diagram artifact declaration is incomplete."
        )
    if schema_version == POLAR_REVALIDATED_DIAGRAM_SCHEMA_VERSION:
        transformation = declaration.get("transformation")
        curve_provenance = declaration.get("curve_provenance")
        if (
            declaration.get("scientific_data_transform") is not True
            or not isinstance(transformation, Mapping)
            or transformation.get("method")
            != "2_degree_centered_angular_bin_mean"
            or transformation.get("bin_width_deg") != 2.0
            or transformation.get("applied_consistently_to_all_four_profiles")
            is not True
            or transformation.get("raw_profiles_preserved") is not True
            or not isinstance(curve_provenance, Mapping)
            or set(curve_provenance) != {panel.key for panel in _PANELS}
        ):
            raise AngularPolarDiagramError(
                "revalidated polar diagram provenance is incomplete."
            )
        for panel in _PANELS:
            raw_curve = curve_provenance[panel.key]
            if (
                not isinstance(raw_curve, Mapping)
                or raw_curve.get("display_title") != panel.title
                or raw_curve.get("panel_label") != panel.label
                or not _is_sha256(raw_curve.get("raw_profile_sha256"))
                or not _is_sha256(
                    raw_curve.get("presentation_profile_sha256")
                )
                or raw_curve.get("scientific_data_transform") is not True
            ):
                raise AngularPolarDiagramError(
                    f"revalidated curve provenance is invalid for {panel.key}."
                )
    for key, (filename, media_type, width, height) in expected.items():
        raw = artifacts[key]
        if (
            not isinstance(raw, Mapping)
            or raw.get("path") != filename
            or raw.get("media_type") != media_type
            or raw.get("width_px") != width
            or raw.get("height_px") != height
            or not _is_sha256(raw.get("sha256"))
        ):
            raise AngularPolarDiagramError(
                f"polar diagram declaration is invalid for {key}."
            )
        unresolved_artifact = root / filename
        artifact = unresolved_artifact.resolve()
        if (
            unresolved_artifact.is_symlink()
            or artifact.parent != root
            or not artifact.is_file()
        ):
            raise AngularPolarDiagramError(
                f"polar diagram artifact is missing or unsafe: {filename}."
            )
        data = artifact.read_bytes()
        if (
            len(data) != raw.get("byte_length")
            or _sha256(data) != raw.get("sha256")
        ):
            raise AngularPolarDiagramError(
                f"polar diagram artifact failed authentication: {filename}."
            )
    png_filenames = [POLAR_PNG_FILENAME]
    if schema_version == POLAR_REVALIDATED_DIAGRAM_SCHEMA_VERSION:
        png_filenames.append(POLAR_RAW_PNG_FILENAME)
    for png_filename in png_filenames:
        if _png_dimensions((root / png_filename).read_bytes()) != (
            POLAR_PNG_WIDTH,
            POLAR_PNG_HEIGHT,
        ):
            raise AngularPolarDiagramError(
                f"polar PNG dimensions are invalid: {png_filename}."
            )


def _revalidated_diagram_declaration(
    *,
    presentation_profiles: Sequence[Mapping[str, object]],
    raw_profiles: Sequence[Mapping[str, object]],
    transformation: Mapping[str, object],
    curve_provenance: Mapping[str, Mapping[str, object]],
    clean_svg: bytes,
    clean_png: bytes,
    raw_svg: bytes,
    raw_png: bytes,
) -> dict[str, object]:
    presentation = _validated_profiles(presentation_profiles)
    raw = _validated_profiles(raw_profiles)

    def artifact(
        path: str,
        media_type: str,
        width: int,
        height: int,
        data: bytes,
        *,
        derived_from: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": path,
            "media_type": media_type,
            "width_px": width,
            "height_px": height,
            "byte_length": len(data),
            "sha256": _sha256(data),
        }
        if derived_from is not None:
            payload["derived_from"] = derived_from
        return payload

    return {
        "schema_id": POLAR_DIAGRAM_SCHEMA_ID,
        "schema_version": POLAR_REVALIDATED_DIAGRAM_SCHEMA_VERSION,
        "layout": "2x2_for_16x9_presentation",
        "quantity": "normalized_completed_aperture_radiant_intensity",
        "scientific_data_transform": True,
        "transformation": dict(transformation),
        "radial_scale": {
            "minimum": 0.0,
            "maximum": 1.0,
            "ticks": list(NORMALIZED_RADIAL_TICKS),
            "identical_in_all_panels": True,
        },
        "artifacts": {
            "authoritative_vector": artifact(
                POLAR_SVG_FILENAME,
                "image/svg+xml",
                POLAR_SVG_WIDTH,
                POLAR_SVG_HEIGHT,
                clean_svg,
            ),
            "high_resolution_raster": artifact(
                POLAR_PNG_FILENAME,
                "image/png",
                POLAR_PNG_WIDTH,
                POLAR_PNG_HEIGHT,
                clean_png,
                derived_from=POLAR_SVG_FILENAME,
            ),
            "raw_audit_vector": artifact(
                POLAR_RAW_SVG_FILENAME,
                "image/svg+xml",
                POLAR_SVG_WIDTH,
                POLAR_SVG_HEIGHT,
                raw_svg,
            ),
            "raw_audit_raster": artifact(
                POLAR_RAW_PNG_FILENAME,
                "image/png",
                POLAR_PNG_WIDTH,
                POLAR_PNG_HEIGHT,
                raw_png,
                derived_from=POLAR_RAW_SVG_FILENAME,
            ),
        },
        "curve_provenance": {
            panel.key: {
                **dict(curve_provenance[panel.key]),
                "panel_label": panel.label,
                "display_title": panel.title,
                "raw_profile_sha256": raw[panel.key]["profile_sha256"],
                "presentation_profile_sha256": presentation[panel.key][
                    "profile_sha256"
                ],
            }
            for panel in _PANELS
        },
    }


def _diagram_declaration(
    profiles: Sequence[Mapping[str, object]],
    svg_bytes: bytes,
    png_bytes: bytes,
) -> dict[str, object]:
    validated = _validated_profiles(profiles)
    return {
        "schema_id": POLAR_DIAGRAM_SCHEMA_ID,
        "schema_version": POLAR_DIAGRAM_SCHEMA_VERSION,
        "layout": "2x2_for_16x9_presentation",
        "quantity": "normalized_completed_aperture_radiant_intensity",
        "rendering": {
            "vector_authority": "deterministic_project_svg_builder",
            "raster_derivation": (
                "Inkscape CLI rasterization of the exact authoritative SVG"
            ),
            "presentation_export_convention": {
                "svg_size_px": [POLAR_SVG_WIDTH, POLAR_SVG_HEIGHT],
                "png_size_px": [POLAR_PNG_WIDTH, POLAR_PNG_HEIGHT],
                "matches_existing_project_16x9_and_4k_convention": True,
            },
            "heavy_plotting_dependency_added": False,
        },
        "radial_scale": {
            "minimum": 0.0,
            "maximum": 1.0,
            "ticks": list(NORMALIZED_RADIAL_TICKS),
            "identical_in_all_panels": True,
        },
        "angular_convention": {
            "optical_axis_deg": 0.0,
            "optical_axis_page_direction": "down",
            "horizon_deg": [-90.0, 90.0],
            "symmetric_profiles_mirrored": True,
        },
        "artifacts": {
            "authoritative_vector": {
                "path": POLAR_SVG_FILENAME,
                "media_type": "image/svg+xml",
                "width_px": POLAR_SVG_WIDTH,
                "height_px": POLAR_SVG_HEIGHT,
                "byte_length": len(svg_bytes),
                "sha256": _sha256(svg_bytes),
            },
            "high_resolution_raster": {
                "path": POLAR_PNG_FILENAME,
                "media_type": "image/png",
                "width_px": POLAR_PNG_WIDTH,
                "height_px": POLAR_PNG_HEIGHT,
                "byte_length": len(png_bytes),
                "sha256": _sha256(png_bytes),
                "derived_from": POLAR_SVG_FILENAME,
                "scientific_data_transform": False,
            },
        },
        "curve_provenance": {
            panel.key: {
                "panel_label": panel.label,
                "display_title": panel.title,
                "profile_sha256": validated[panel.key]["profile_sha256"],
                "characterization_identity_sha256": validated[panel.key].get(
                    "characterization_identity_sha256"
                ),
                "source_authority": validated[panel.key]["source_authority"],
                "source_arrays": {
                    "angle_deg_field": "polar_profiles[].angle_deg",
                    "normalized_intensity_field": (
                        "polar_profiles[].normalized_radiant_intensity"
                    ),
                    "copied_or_synthetic_plotting_dataset": False,
                },
            }
            for panel in _PANELS
        },
    }


def _panel_svg(
    panel: _PanelSpec,
    profile: Mapping[str, object],
    *,
    fwhm_label_decimals: int,
) -> list[str]:
    title_y = panel.center_y - 82.0
    radius = _PANEL_RADIUS
    parts = [
        (
            f'<g id="panel-{panel.key}" class="polar-panel" '
            f'data-profile-key="{panel.key}" data-radial-min="0" '
            f'data-radial-max="1" data-radial-ticks="0.25,0.5,0.75,1" '
            f'data-profile-sha256="{profile["profile_sha256"]}">'
        ),
        (
            f'<text x="{_number(panel.center_x)}" y="{_number(title_y)}" '
            'text-anchor="middle" '
            f'font-family="{_FONT_STACK}" font-size="27" font-weight="600" '
            f'fill="#111111">{escape(panel.label + " " + panel.title)}</text>'
        ),
    ]
    for fraction in NORMALIZED_RADIAL_TICKS:
        points = " ".join(
            _polar_point(
                panel.center_x,
                panel.center_y,
                radius * fraction,
                angle,
            )
            for angle in range(-90, 91, 3)
        )
        parts.append(
            f'<polyline class="radial-grid" data-radial-value="{fraction:g}" '
            f'points="{points}" fill="none" stroke="{_GRID_COLOR}" '
            'stroke-width="1.2"/>'
        )
    for angle in (-90, -60, -30, 0, 30, 60, 90):
        endpoint = _polar_xy(
            panel.center_x, panel.center_y, radius, float(angle)
        )
        parts.append(
            f'<line class="angular-grid" data-angle-deg="{angle}" '
            f'x1="{_number(panel.center_x)}" y1="{_number(panel.center_y)}" '
            f'x2="{_number(endpoint[0])}" y2="{_number(endpoint[1])}" '
            f'stroke="{_GRID_COLOR}" stroke-width="1.2"/>'
        )
    parts.extend(
        (
            _angle_label(panel, -90.0, "−90°", -18.0, 7.0),
            _angle_label(panel, -60.0, "−60°", -19.0, 2.0),
            _angle_label(panel, -30.0, "−30°", -9.0, 10.0),
            _angle_label(panel, 0.0, "0°", 0.0, 25.0),
            _angle_label(panel, 30.0, "30°", 9.0, 10.0),
            _angle_label(panel, 60.0, "60°", 19.0, 2.0),
            _angle_label(panel, 90.0, "90°", 18.0, 7.0),
        )
    )
    for fraction in NORMALIZED_RADIAL_TICKS:
        parts.append(
            f'<text x="{_number(panel.center_x + 8.0)}" '
            f'y="{_number(panel.center_y + radius * fraction - 5.0)}" '
            f'font-family="{_FONT_STACK}" font-size="15" fill="#777777">'
            f"{fraction:g}</text>"
        )
    angles = profile["angle_deg"]
    intensities = profile["normalized_radiant_intensity"]
    assert isinstance(angles, list)
    assert isinstance(intensities, list)
    mirrored = [
        (-float(angle), float(value))
        for angle, value in reversed(tuple(zip(angles[1:], intensities[1:], strict=True)))
    ] + [
        (float(angle), float(value))
        for angle, value in zip(angles, intensities, strict=True)
    ]
    curve_points = " ".join(
        _polar_point(
            panel.center_x,
            panel.center_y,
            radius * value,
            angle,
        )
        for angle, value in mirrored
    )
    source_angles = escape(
        json.dumps(angles, separators=(",", ":"), allow_nan=False),
        quote=True,
    )
    source_intensities = escape(
        json.dumps(intensities, separators=(",", ":"), allow_nan=False),
        quote=True,
    )
    characterization_identity = profile.get("characterization_identity_sha256")
    parts.extend(
        (
            (
                f'<polyline id="curve-{panel.key}" class="distribution-curve" '
                f'data-profile-key="{panel.key}" '
                f'data-profile-sha256="{profile["profile_sha256"]}" '
                f'data-characterization-identity-sha256="'
                f'{escape(str(characterization_identity or "not-applicable-ies-authority"))}" '
                f'data-authoritative-angle-deg="{source_angles}" '
                f'data-authoritative-normalized-intensity="{source_intensities}" '
                f'points="{curve_points}" fill="none" stroke="{_CURVE_COLOR}" '
                'stroke-width="4.25" stroke-linecap="round" '
                'stroke-linejoin="round"/>'
            ),
            (
                f'<text x="{_number(panel.center_x)}" '
                f'y="{_number(panel.center_y + radius + 60.0)}" '
                'text-anchor="middle" '
                f'font-family="{_FONT_STACK}" font-size="21" fill="#222222">'
                f'Measured FWHM: '
                f'{float(profile["measured_fwhm_deg"]):.{fwhm_label_decimals}f}°'
                "</text>"
            ),
            "</g>",
        )
    )
    return parts


def _angle_label(
    panel: _PanelSpec,
    angle: float,
    label: str,
    offset_x: float,
    offset_y: float,
) -> str:
    x, y = _polar_xy(
        panel.center_x,
        panel.center_y,
        _PANEL_RADIUS,
        angle,
    )
    return (
        f'<text x="{_number(x + offset_x)}" y="{_number(y + offset_y)}" '
        f'text-anchor="middle" font-family="{_FONT_STACK}" '
        f'font-size="16" fill="#555555">{label}</text>'
    )


def _validated_profiles(
    profiles: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    by_key: dict[str, dict[str, object]] = {}
    for raw in profiles:
        key = raw.get("profile_key")
        if not isinstance(key, str) or key in by_key:
            raise AngularPolarDiagramError("polar profiles have invalid keys.")
        by_key[key] = dict(raw)
    expected_keys = {panel.key for panel in _PANELS}
    if set(by_key) != expected_keys:
        raise AngularPolarDiagramError(
            "polar diagram requires exactly the four preregistered profiles."
        )
    for panel in _PANELS:
        profile = by_key[panel.key]
        if profile.get("display_title") != panel.title:
            raise AngularPolarDiagramError(
                f"polar profile title disagrees for {panel.key}."
            )
        angles = profile.get("angle_deg")
        intensities = profile.get("normalized_radiant_intensity")
        if (
            not isinstance(angles, list)
            or not isinstance(intensities, list)
            or len(angles) != len(intensities)
            or len(angles) < 2
        ):
            raise AngularPolarDiagramError(
                f"polar profile arrays are malformed for {panel.key}."
            )
        try:
            numeric_angles = [float(value) for value in angles]
            numeric_intensities = [float(value) for value in intensities]
        except (TypeError, ValueError) as error:
            raise AngularPolarDiagramError(
                f"polar profile arrays are non-numeric for {panel.key}."
            ) from error
        if (
            numeric_angles[0] != 0.0
            or numeric_angles[-1] != 90.0
            or any(
                second <= first
                for first, second in zip(numeric_angles, numeric_angles[1:])
            )
            or any(
                not math.isfinite(value) or value < 0.0
                for value in numeric_intensities
            )
        ):
            raise AngularPolarDiagramError(
                f"polar profile domain or normalization is invalid for {panel.key}."
            )
        actual_profile_sha256 = profile_sha256(
            numeric_angles, numeric_intensities
        )
        if profile.get("profile_sha256") != actual_profile_sha256:
            raise AngularPolarDiagramError(
                f"polar profile identity is stale for {panel.key}."
            )
        optical_axis_reference = (
            profile.get("normalization_reference")
            == "optical_axis_intensity"
        )
        if optical_axis_reference:
            if not math.isclose(
                numeric_intensities[0], 1.0, rel_tol=0.0, abs_tol=1e-12
            ):
                raise AngularPolarDiagramError(
                    f"polar optical-axis normalization is invalid for "
                    f"{panel.key}."
                )
        elif not math.isclose(
            max(numeric_intensities), 1.0, rel_tol=0.0, abs_tol=1e-12
        ):
            raise AngularPolarDiagramError(
                f"polar peak normalization is invalid for {panel.key}."
            )
        try:
            actual_fwhm = (
                measured_optical_axis_reference_fwhm_deg(
                    numeric_angles, numeric_intensities
                )
                if optical_axis_reference
                else measured_fwhm_deg(
                    numeric_angles, numeric_intensities
                )
            )
            declared_fwhm = float(profile["measured_fwhm_deg"])
        except (KeyError, TypeError, ValueError) as error:
            raise AngularPolarDiagramError(
                f"polar profile FWHM is invalid for {panel.key}."
            ) from error
        if not math.isclose(
            actual_fwhm, declared_fwhm, rel_tol=0.0, abs_tol=1e-9
        ):
            raise AngularPolarDiagramError(
                f"polar profile FWHM is stale for {panel.key}."
            )
        if not isinstance(profile.get("source_authority"), Mapping):
            raise AngularPolarDiagramError(
                f"polar profile source authority is missing for {panel.key}."
            )
        if panel.key != "conventional" and not _is_sha256(
            profile.get("characterization_identity_sha256")
        ):
            raise AngularPolarDiagramError(
                f"polar characterization identity is missing for {panel.key}."
            )
        profile["angle_deg"] = numeric_angles
        profile["normalized_radiant_intensity"] = numeric_intensities
    return by_key


def _polar_point(
    center_x: float,
    center_y: float,
    radius: float,
    angle_deg: float,
) -> str:
    x, y = _polar_xy(center_x, center_y, radius, angle_deg)
    return f"{_number(x)},{_number(y)}"


def _polar_xy(
    center_x: float,
    center_y: float,
    radius: float,
    angle_deg: float,
) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return (
        center_x + radius * math.sin(angle),
        center_y + radius * math.cos(angle),
    )


def _number(value: float, digits: int = 6) -> str:
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
