#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from rad_rebuild.radiance.engine.emitters.hps_generation.geometry import (
    compute_fixture_layout as _geometry_compute_fixture_layout,
    fixture_positions,
)
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    BARE_LAMP_INITIAL_PPF_UMOL_S,
    BARE_LAMP_TOTAL_LUMINOUS_FLUX_LM,
    DEFAULT_COVERAGE_FT,
    DEFAULT_IES_VARIANT,
    DEFAULT_MOUNT_Z_M,
    DEFAULT_OUTER_MARGIN_IN,
    NOMINAL_FIXTURE_PPE_UMOL_PER_J,
    NOMINAL_INPUT_WATTS,
    get_ies_comparator_profile,
    normalize_ies_variant,
    validate_coverage_ft,
)
from rad_rebuild.radiance.engine.emitters.hps_generation.rad_writer import write_hps_rad
from rad_rebuild.radiance.engine.emitters.hps_generation.summary import (
    completion_lines,
    hps_base_summary_text,
    hps_ies_summary_text,
    hps_summary_text,
    summary_float,
    summary_int,
    summary_text,
    write_summary_files,
)
from rad_rebuild.radiance.engine.photometry.ies_photon_toolkit import (
    inspect_ies_rad_source,
    normalize_ies_rad_companion_paths,
    parse_ies_numeric_header,
    read_ies_lines,
    run_ies2rad,
    spd_photon_metrics,
    summarize_ies_header,
)
from rad_rebuild.radiance.paths import RADIANCE_DATA_ROOT


OUT_DIR = Path(os.getenv("RADIANCE_RUNTIME_STATE_ROOT", "runtime_state"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = RADIANCE_DATA_ROOT

IES_VARIANT = normalize_ies_variant(os.getenv("HPS_IES_VARIANT", DEFAULT_IES_VARIANT))
ACTIVE_IES_PROFILE = get_ies_comparator_profile(IES_VARIANT)
HPS_IES_LABEL = f"{ACTIVE_IES_PROFILE.label} IES+SPD comparator"
IES_PATH = Path(
    os.getenv("HPS_IES_PATH", str(DATA_DIR / ACTIVE_IES_PROFILE.default_ies_file))
)
IES_SPD_PATH = Path(
    os.getenv("HPS_IES_SPD_PATH", str(DATA_DIR / ACTIVE_IES_PROFILE.default_spd_file))
)
IES_BASENAME = os.getenv("HPS_IES_BASENAME", IES_PATH.stem)
IES_ROT_X_DEG = float(os.getenv("HPS_IES_ROT_X_DEG", "0"))
IES_ROT_Z_DEG = float(os.getenv("HPS_IES_ROT_Z_DEG", "0"))

ACTIVE_FIXTURE_LENGTH_M = ACTIVE_IES_PROFILE.fixture_length_m
ACTIVE_FIXTURE_WIDTH_M = ACTIVE_IES_PROFILE.fixture_width_m
ACTIVE_FIXTURE_HEIGHT_M = ACTIVE_IES_PROFILE.fixture_height_m
JsonObject = dict[str, Any]


def _clamp_0_1(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def compute_fixture_layout() -> JsonObject:
    return _geometry_compute_fixture_layout(
        length_ft=float(os.getenv("LENGTH_FT", "12")),
        width_ft=float(os.getenv("WIDTH_FT", "12")),
        margin_in=float(os.getenv("MARGIN_IN", f"{DEFAULT_OUTER_MARGIN_IN:g}")),
        coverage_ft=validate_coverage_ft(
            float(os.getenv("HPS_COVERAGE_FT", f"{DEFAULT_COVERAGE_FT:g}"))
        ),
        mount_z_m=float(os.getenv("HPS_Z_M", f"{DEFAULT_MOUNT_Z_M:g}")),
        align_long_axis_x=os.getenv("ALIGN_LONG_AXIS_X", "1") == "1",
        profile=ACTIVE_IES_PROFILE,
    )


def get_fixture_positions() -> list[dict[str, float]]:
    return fixture_positions(compute_fixture_layout())


def _default_ies_fixture_anchor(_total_luminaire_lumens: float) -> float:
    return ACTIVE_IES_PROFILE.nominal_fixture_ppf_umol_s


def _hps_fixture_anchor(total_luminaire_lumens: float) -> tuple[float, str, float]:
    raw = os.getenv("HPS_FIXTURE_PPF", "").strip()
    default_anchor = _default_ies_fixture_anchor(total_luminaire_lumens)
    if raw:
        return float(raw), "explicit HPS_FIXTURE_PPF override", default_anchor
    return (
        default_anchor,
        f"default shared HPS fixture efficacy target ({ACTIVE_IES_PROFILE.nominal_input_watts:.1f} W * "
        f"{ACTIVE_IES_PROFILE.nominal_fixture_ppe_umol_per_j:.2f} umol/J)",
        default_anchor,
    )


def _hps_input_watts(ies_input_watts: float | None) -> tuple[float, str]:
    raw = os.getenv("HPS_INPUT_WATTS", "").strip()
    if raw:
        return float(raw), "explicit HPS_INPUT_WATTS override"
    if ies_input_watts and ies_input_watts > 0:
        return float(ies_input_watts), "IES header input watts"
    return NOMINAL_INPUT_WATTS, "profile default input watts"


def _hps_ies_layout_metadata(
    *,
    ies_summary: Any,
    ies_source_meta: JsonObject,
    source_correction_function: object,
    source_primitive_type: object,
    source_primitive_count: int,
    zero_opening: bool,
    ies_lm_to_umol: float,
    pre_norm_fixture_ppf: float,
    default_anchor_ppf: float,
    fixture_anchor_ppf: float,
    anchor_authority: str,
    input_watts: float,
    input_watts_authority: str,
    fixture_anchor_active: float,
    ies_scale: float,
    ies_baseline_radiant_w: float,
    ies_target_radiant_w: float,
    spd_metrics: Any,
    anchor_notes: list[str],
) -> JsonObject:
    return {
        "model_label": HPS_IES_LABEL,
        "fixture_emitter_interpretation": "single_whole_fixture_ies_emitter",
        "reference_geometry": "fixture body outline and lamp axis retained in layout JSON only",
        "ies_mode": True,
        "ies_variant": ACTIVE_IES_PROFILE.variant,
        "ies_variant_profile": ACTIVE_IES_PROFILE.profile_version,
        "directional_model": "whole_fixture_ies_photometry",
        "ies_file": str(IES_PATH),
        "ies_spd_path": str(IES_SPD_PATH),
        "ies_rot_x_deg": IES_ROT_X_DEG,
        "ies_rot_z_deg": IES_ROT_Z_DEG,
        "ies_header_summary": {
            "lamp_count": ies_summary.lamp_count,
            "lumens_per_lamp_lm": ies_summary.lumens_per_lamp,
            "total_luminaire_lumens_lm": ies_summary.total_luminaire_lumens,
            "input_watts": ies_summary.input_watts,
            "luminous_opening_m": [
                ies_summary.width_m,
                ies_summary.length_m,
                ies_summary.height_m,
            ],
        },
        "ies_zero_luminous_opening": zero_opening,
        "raw_ies2rad_geometry": ies_source_meta["raw_geometry"],
        "ies_source_geometry": ies_source_meta["geometry"],
        "ies_source_correction_function": source_correction_function,
        "ies_source_primitive_type": source_primitive_type,
        "ies_source_primitive_count": source_primitive_count,
        "ies_source_geometry_note": (
            "The KARMA horticultural IES provides a usable luminous opening, so the native "
            "ies2rad source primitive is retained as the active emitter."
        ),
        "ies_source_aperture": {
            "name": ies_source_meta["aperture_name"],
            "length_m": ies_source_meta["aperture_length_m"],
            "width_m": ies_source_meta["aperture_width_m"],
            "z_offset_m": ies_source_meta["aperture_z_m"],
            "primitive_count_replaced": ies_source_meta["primitive_count"],
            "faces_removed": ies_source_meta["faces_removed"],
            "manual_aperture": ies_source_meta["manual_aperture"],
        },
        "bare_lamp_reference": {
            "ppf_umol_s": BARE_LAMP_INITIAL_PPF_UMOL_S,
            "total_luminous_flux_lm": BARE_LAMP_TOTAL_LUMINOUS_FLUX_LM,
        },
        "ies_lm_to_umol": ies_lm_to_umol,
        "ies_lm_to_umol_method": f"digitized relative SPD ({IES_SPD_PATH.name}, wavelength-aware PAR/lumen bridge)",
        "pre_normalization_fixture_ppf_umol_s": pre_norm_fixture_ppf,
        "default_fixture_anchor_umol_s": default_anchor_ppf,
        "fixture_ppf_anchor_umol_s": fixture_anchor_ppf,
        "fixture_ppf_anchor_authority": anchor_authority,
        "fixture_input_w_reference": input_watts,
        "fixture_input_w_authority": input_watts_authority,
        "active_fixture_ppf_umol_s": fixture_anchor_active,
        "fixture_ppe_umol_j": fixture_anchor_ppf / input_watts
        if input_watts > 0
        else NOMINAL_FIXTURE_PPE_UMOL_PER_J,
        "ies_scale": ies_scale,
        "ies_baseline_radiant_w": ies_baseline_radiant_w,
        "ies_target_radiant_w": ies_target_radiant_w,
        "spd_metrics": {
            "sample_count": spd_metrics.sample_count,
            "par_fraction_of_total_power": spd_metrics.par_fraction_of_total_power,
            "par_centroid_nm": spd_metrics.par_centroid_nm,
            "luminous_efficacy_lm_per_radiant_w": spd_metrics.luminous_efficacy_lm_per_radiant_w,
            "par_photon_umol_per_radiant_w": spd_metrics.par_photon_umol_per_radiant_w,
            "umol_per_lumen": spd_metrics.umol_per_lumen,
        },
        "normalization_path_summary": (
            "IES total luminaire lumens x SPD-derived umol/lm gives the pre-normalization fixture "
            "PPF. That result is then normalized to the explicit fixture anchor rather than inheriting "
            "a source-file lumen default."
        ),
        "active_ies_geometry_builder": "native IES luminous opening dimensions",
        "anchor_notes": anchor_notes,
    }


def _hps_ies_summary_lines(
    *,
    layout: JsonObject,
    ies_summary: Any,
    fixture_anchor_ppf: float,
    fixture_anchor_active: float,
    input_watts: float,
    ies_lm_to_umol: float,
    pre_norm_fixture_ppf: float,
    default_anchor_ppf: float,
    anchor_authority: str,
    eff_scale: float,
    ies_scale: float,
    ies_source_meta: JsonObject,
    source_correction_function: object,
    source_primitive_type: object,
    source_primitive_count: int,
    source_geometry_mode: str,
    anchor_notes: list[str],
) -> JsonObject:
    fixture_count = len(layout["fixtures"])
    summary_lines: JsonObject = {
        "model_label": HPS_IES_LABEL,
        "model_mode": "whole_fixture_ies_comparator",
        "profile": ACTIVE_IES_PROFILE.profile_version,
        "archetype": ACTIVE_IES_PROFILE.archetype,
        "ies_variant": ACTIVE_IES_PROFILE.variant,
        "ies_file": IES_PATH.name,
        "fixtures": fixture_count,
        "coverage_ft": float(layout["coverage_ft"]),
        "fixture_ppf": fixture_anchor_ppf,
        "active_fixture_ppf_umol_s": fixture_anchor_active,
        "fixture_input_w": input_watts,
        "fixture_ppe": fixture_anchor_ppf / input_watts
        if input_watts > 0
        else NOMINAL_FIXTURE_PPE_UMOL_PER_J,
        "total_ppf": fixture_anchor_active * fixture_count,
        "total_w": input_watts * fixture_count * eff_scale,
        "fixture_count": fixture_count,
        "ies_lumens_per_lamp_lm": ies_summary.lumens_per_lamp,
        "ies_total_luminaire_lumens_lm": ies_summary.total_luminaire_lumens,
        "ies_input_watts": ies_summary.input_watts
        if ies_summary.input_watts is not None
        else 0.0,
        "spd_umol_per_lumen": ies_lm_to_umol,
        "pre_normalization_fixture_ppf_umol_s": pre_norm_fixture_ppf,
        "default_fixture_anchor_umol_s": default_anchor_ppf,
        "fixture_anchor_authority": anchor_authority,
        "final_scale_multiplier": ies_scale,
        "effective_aperture_length_m": float(ies_source_meta["aperture_length_m"]),
        "effective_aperture_width_m": float(ies_source_meta["aperture_width_m"]),
        "effective_aperture_z_offset_m": float(ies_source_meta["aperture_z_m"]),
        "source_correction_function": source_correction_function,
        "source_primitive_type": source_primitive_type,
        "source_primitive_count": source_primitive_count,
        "source_geometry_mode": source_geometry_mode,
    }
    if anchor_notes:
        summary_lines["anchor_notes"] = " | ".join(anchor_notes)
    return summary_lines


def _write_ies_comparator(
    out: Path,
    layout: JsonObject,
    eff_scale: float,
) -> tuple[str, JsonObject, JsonObject]:
    ies_lines = read_ies_lines(IES_PATH)
    ies_numeric = parse_ies_numeric_header(ies_lines)
    ies_summary = summarize_ies_header(IES_PATH, ies_lines)
    spd_metrics = spd_photon_metrics(IES_SPD_PATH)
    ies_lm_to_umol = spd_metrics.umol_per_lumen
    pre_norm_fixture_ppf = ies_summary.total_luminaire_lumens * ies_lm_to_umol
    if pre_norm_fixture_ppf <= 0:
        raise SystemExit(
            "ERROR: HPS IES+SPD pre-normalization photon output is invalid."
        )

    fixture_anchor_ppf, anchor_authority, default_anchor_ppf = _hps_fixture_anchor(
        ies_summary.total_luminaire_lumens
    )
    fixture_anchor_active = fixture_anchor_ppf * eff_scale
    input_watts, input_watts_authority = _hps_input_watts(ies_summary.input_watts)
    ies_scale = fixture_anchor_active / pre_norm_fixture_ppf
    ies_target_radiant_w = (
        fixture_anchor_active / spd_metrics.par_photon_umol_per_radiant_w
    )
    ies_baseline_radiant_w = (
        ies_summary.total_luminaire_lumens
        / spd_metrics.luminous_efficacy_lm_per_radiant_w
    )

    zero_opening = (
        abs(ies_numeric.width_m) <= 1e-9
        and abs(ies_numeric.length_m) <= 1e-9
        and abs(ies_numeric.height_m) <= 1e-9
    )
    if zero_opening:
        raise SystemExit(
            "ERROR: active KARMA HPS IES must provide non-zero luminous opening dimensions."
        )

    ies_rad_path = run_ies2rad(OUT_DIR, IES_PATH, IES_BASENAME, ies_scale)
    normalize_ies_rad_companion_paths(ies_rad_path)
    native_meta = cast(JsonObject, inspect_ies_rad_source(ies_rad_path))
    dims = native_meta["dimensions_m"]
    center = native_meta["center_m"]
    ies_source_meta: JsonObject = {
        "modifier": native_meta["modifier"],
        "light": native_meta["light"],
        "light_kind": native_meta["light_kind"],
        "correction_function": native_meta["correction_function"],
        "geometry": f"native_{native_meta['geometry']}",
        "aperture_name": native_meta["primitive_names"][0],
        "aperture_length_m": float(dims[0]),
        "aperture_width_m": float(dims[1]),
        "aperture_z_m": float(center[2]),
        "faces_removed": 0,
        "primitive_count": int(native_meta["primitive_count"]),
        "raw_geometry": str(native_meta["geometry"]),
        "raw_sphere_count": 1 if native_meta["primitive_type"] == "sphere" else 0,
        "manual_aperture": False,
        "primitive_type": native_meta["primitive_type"],
        "primitive_names": native_meta["primitive_names"],
    }
    ies_rad_rel = ies_rad_path.as_posix()
    anchor_notes: list[str] = []
    if fixture_anchor_ppf > BARE_LAMP_INITIAL_PPF_UMOL_S + 1e-6:
        anchor_notes.append(
            f"Final anchor {fixture_anchor_ppf:.3f} umol/s exceeds the supplied bare-lamp "
            f"class PPF of {BARE_LAMP_INITIAL_PPF_UMOL_S:.1f} umol/s because {anchor_authority} "
            "was treated as the final normalization authority."
        )
    source_correction_function = ies_source_meta.get("correction_function", "flatcorr")
    source_primitive_type = ies_source_meta.get("primitive_type", "polygon")
    source_primitive_count = int(ies_source_meta.get("primitive_count", 1))
    source_geometry_mode = str(
        ies_source_meta.get("geometry", "single_downward_flatcorr_aperture")
    )

    layout.update(
        _hps_ies_layout_metadata(
            ies_summary=ies_summary,
            ies_source_meta=ies_source_meta,
            source_correction_function=source_correction_function,
            source_primitive_type=source_primitive_type,
            source_primitive_count=source_primitive_count,
            zero_opening=zero_opening,
            ies_lm_to_umol=ies_lm_to_umol,
            pre_norm_fixture_ppf=pre_norm_fixture_ppf,
            default_anchor_ppf=default_anchor_ppf,
            fixture_anchor_ppf=fixture_anchor_ppf,
            anchor_authority=anchor_authority,
            input_watts=input_watts,
            input_watts_authority=input_watts_authority,
            fixture_anchor_active=fixture_anchor_active,
            ies_scale=ies_scale,
            ies_baseline_radiant_w=ies_baseline_radiant_w,
            ies_target_radiant_w=ies_target_radiant_w,
            spd_metrics=spd_metrics,
            anchor_notes=anchor_notes,
        )
    )

    writer_source_meta = {
        **ies_source_meta,
        "source_geometry_mode": source_geometry_mode,
        "source_correction_function": source_correction_function,
        "source_primitive_type": source_primitive_type,
        "source_primitive_count": source_primitive_count,
    }
    write_hps_rad(
        out=out,
        layout=layout,
        model_label=HPS_IES_LABEL,
        profile=ACTIVE_IES_PROFILE,
        ies_path=IES_PATH,
        ies_spd_path=IES_SPD_PATH,
        eff_scale=eff_scale,
        lumens_per_lamp=ies_summary.lumens_per_lamp,
        total_luminaire_lumens=ies_summary.total_luminaire_lumens,
        input_watts=input_watts,
        zero_opening=zero_opening,
        source_meta=writer_source_meta,
        pre_norm_fixture_ppf=pre_norm_fixture_ppf,
        fixture_anchor_ppf=fixture_anchor_ppf,
        ies_scale=ies_scale,
        ies_rad_rel=ies_rad_rel,
        ies_rot_x_deg=IES_ROT_X_DEG,
        ies_rot_z_deg=IES_ROT_Z_DEG,
    )

    summary_lines = _hps_ies_summary_lines(
        layout=layout,
        ies_summary=ies_summary,
        fixture_anchor_ppf=fixture_anchor_ppf,
        fixture_anchor_active=fixture_anchor_active,
        input_watts=input_watts,
        ies_lm_to_umol=ies_lm_to_umol,
        pre_norm_fixture_ppf=pre_norm_fixture_ppf,
        default_anchor_ppf=default_anchor_ppf,
        anchor_authority=anchor_authority,
        eff_scale=eff_scale,
        ies_scale=ies_scale,
        ies_source_meta=ies_source_meta,
        source_correction_function=source_correction_function,
        source_primitive_type=source_primitive_type,
        source_primitive_count=source_primitive_count,
        source_geometry_mode=source_geometry_mode,
        anchor_notes=anchor_notes,
    )
    return HPS_IES_LABEL, summary_lines, layout


def _summary_float(summary_lines: JsonObject, key: str) -> float:
    return summary_float(summary_lines, key)


def _summary_int(summary_lines: JsonObject, key: str) -> int:
    return summary_int(summary_lines, key)


def _summary_text(summary_lines: JsonObject, key: str) -> str:
    return summary_text(summary_lines, key)


def _hps_base_summary_text(summary_lines: JsonObject, layout: JsonObject) -> list[str]:
    return hps_base_summary_text(summary_lines, layout)


def _hps_ies_summary_text(summary_lines: JsonObject) -> list[str]:
    return hps_ies_summary_text(summary_lines)


def _hps_summary_text(summary_lines: JsonObject, layout: JsonObject) -> list[str]:
    return hps_summary_text(summary_lines, layout)


def _write_summary_files(summary_lines: JsonObject, layout: JsonObject) -> None:
    write_summary_files(out_dir=OUT_DIR, summary_lines=summary_lines, layout=layout)


def _print_hps_completion(out: Path, model_label: str, layout: JsonObject) -> None:
    for line in completion_lines(
        out=out,
        out_dir=OUT_DIR,
        model_label=model_label,
        layout=layout,
        ies_path=IES_PATH,
        ies_spd_path=IES_SPD_PATH,
    ):
        print(line)


def main() -> None:
    out = OUT_DIR / "emitters_hps_ALL_umol.rad"
    layout = compute_fixture_layout()
    eff_scale = _clamp_0_1(float(os.getenv("EFF_SCALE", "1.0")))

    model_label, summary_lines, layout = _write_ies_comparator(out, layout, eff_scale)

    _write_summary_files(summary_lines, layout)

    _print_hps_completion(out, model_label, layout)


if __name__ == "__main__":
    main()
