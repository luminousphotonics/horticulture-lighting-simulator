from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.engine.emitters.spydr3_generation.geometry import (
    SpydrFixtureGeometry,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.ies_stage import (
    SpydrIesStage,
)

JsonObject = dict[str, Any]


def spydr_summary_detail_line(
    *,
    stage: SpydrIesStage,
    model_label: str,
    ies_path: Path,
    ies_spd_path: Path,
    ppf_fixture: float,
    ies_rot_x_deg: float,
    ies_rot_z_deg: float,
) -> str:
    return (
        f"MODEL_LABEL={model_label}  DIRECTIONAL_MODEL=whole_fixture_ies_photometry  "
        f"WHOLE_FIXTURE_IES=1  IES_FILE={ies_path}  IES_SCALE={stage.scale:.6g}  "
        f"IES_SOURCE_GEOMETRY={stage.source_meta['geometry']}  "
        f"IES_LM_TO_UMOL={stage.lm_to_umol:.6g}  "
        f"IES_LM_TO_UMOL_METHOD={stage.lm_to_umol_method}  "
        f"SPD_FILE={ies_spd_path.name}  SPD_PAR_UMOL_PER_RADIANT_W={stage.spd_metrics.par_photon_umol_per_radiant_w:.6g}  "
        f"SPD_LM_PER_RADIANT_W={stage.spd_metrics.luminous_efficacy_lm_per_radiant_w:.6g}  "
        f"PPF_ANCHOR_UMOL_S={ppf_fixture:.6g}  IES_ROT_X_DEG={ies_rot_x_deg:g}  "
        f"IES_ROT_Z_DEG={ies_rot_z_deg:g}\n"
    )


def build_spydr_summary_text(
    *,
    layout: JsonObject,
    geometry: SpydrFixtureGeometry,
    stage: SpydrIesStage,
    model_label: str,
    bars: int,
    ppf_fixture: float,
    derate: float,
    ies_path: Path,
    ies_spd_path: Path,
    ies_rot_x_deg: float,
    ies_rot_z_deg: float,
) -> str:
    total_ppf = geometry.fixture_ppf_active * len(layout["fixtures"])
    return (
        f"{model_label}\n"
        f"fixtures={len(layout['fixtures'])}  bars/fixture={bars}\n"
        f"layout_mode={layout['layout_mode']}  pitch_x_in={layout.get('pitch_x_in') or 0:.2f}  "
        f"pitch_y_in={layout.get('pitch_y_in') or 0:.2f}\n"
        f"PPF/fixture={ppf_fixture:.0f} umol/s  PPF_IS_SYSTEM=1  derate(EFF_SCALE)={derate:.3f}\n"
        f"PPE/fixture={geometry.fixture_ppe:.3f} umol/J  input_power/fixture={geometry.fixture_input_w:.3f} W\n"
        f"TOTAL PPF ≈ {total_ppf:.0f} umol/s\n"
        f"fixture_input_w={geometry.fixture_input_w:.6f}\n"
        f"fixture_ppf={ppf_fixture:.6f}\n"
        f"fixture_ppe={geometry.fixture_ppe:.6f}\n"
        f"ies_file={ies_path.name}\n"
        f"spd_file={ies_spd_path.name}\n"
        f"{spydr_summary_detail_line(stage=stage, model_label=model_label, ies_path=ies_path, ies_spd_path=ies_spd_path, ppf_fixture=ppf_fixture, ies_rot_x_deg=ies_rot_x_deg, ies_rot_z_deg=ies_rot_z_deg)}"
    )


def apply_spydr_ies_layout_metadata(
    *,
    layout: JsonObject,
    stage: SpydrIesStage,
    ies_spd_path: Path,
    ies_rot_x_deg: float,
    ies_rot_z_deg: float,
    default_fixture_ppf_umol_s: float,
) -> None:
    layout["directional_model"] = "whole_fixture_ies_photometry"
    layout["ies_source_geometry"] = stage.source_meta["geometry"]
    layout["ies_source_geometry_note"] = (
        "The ies2rad six-face luminous box is rewritten into a single downward "
        "flatcorr aperture so the whole-fixture photometry is emitted from one "
        "downward-facing horticultural luminaire aperture rather than from top "
        "and side faces."
    )
    layout["ies_source_aperture"] = {
        "name": stage.source_meta["aperture_name"],
        "length_m": stage.source_meta["aperture_length_m"],
        "width_m": stage.source_meta["aperture_width_m"],
        "z_m": stage.source_meta["aperture_z_m"],
        "original_box_face_count": stage.source_meta["box_face_count"],
        "removed_faces": stage.source_meta["faces_removed"],
    }
    layout["ies_lumens"] = stage.lumens
    layout["ies_baseline_ppf_umol_s"] = stage.baseline_ppf
    layout["ies_lm_to_umol"] = stage.lm_to_umol
    layout["ies_lm_to_umol_method"] = stage.lm_to_umol_method
    layout["ies_spd_path"] = str(ies_spd_path)
    layout["ies_dims_m"] = stage.dims_m
    layout["ies_rot_x_deg"] = ies_rot_x_deg
    layout["ies_rot_z_deg"] = ies_rot_z_deg
    layout["ies_baseline_radiant_w"] = stage.baseline_radiant_w
    layout["ies_target_radiant_w"] = stage.target_radiant_w
    layout["ies_scale"] = stage.scale
    layout["spd_metrics"] = asdict(stage.spd_metrics)
    layout["photon_normalization_note"] = (
        "Angular distribution comes from the whole-fixture IES. The digitized "
        "relative SPD supplies the active wavelength-aware lumen-to-photon bridge "
        "and radiant-watt to photon conversion, and the final fixture output is "
        "modernized only through the absolute normalization anchor while the "
        "field shape remains unchanged. It is "
        f"normalized to the default {default_fixture_ppf_umol_s:.2f} umol/s PPF anchor."
    )


def spydr_completion_lines(
    *,
    out: Path,
    geometry: SpydrFixtureGeometry,
    stage: SpydrIesStage,
    ppf_fixture: float,
) -> tuple[str, ...]:
    return (
        "Conventional LED whole-fixture IES comparator: "
        f"PPF anchor={ppf_fixture:.1f} umol/s, input_ref={geometry.fixture_input_w:.3f} W, "
        f"SPD photon efficacy={stage.spd_metrics.par_photon_umol_per_radiant_w:.6f} umol/J_radiant",
        "IES interpretation: one active full-fixture emitter per luminaire; "
        "8 bars retained only as non-emitting reference geometry",
        "Active Radiance source: single downward flatcorr aperture "
        f"(rewritten from the original {stage.source_meta['box_face_count']}-face ies2rad box emitter)",
        "SPD use: wavelength-aware radiant-watt to photon conversion for the "
        f"{stage.lm_to_umol:.9f} umol/lm bridge ({stage.lm_to_umol_method})",
        f"✔ Wrote {out}",
        "✔ Wrote conventional fixture summary",
        "✔ Wrote conventional layout JSON",
    )
