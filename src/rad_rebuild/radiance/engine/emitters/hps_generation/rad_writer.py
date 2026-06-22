from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    BARE_LAMP_INITIAL_PPF_UMOL_S,
    BARE_LAMP_TOTAL_LUMINOUS_FLUX_LM,
    IN2M,
    HpsIesComparatorProfile,
)

JsonObject = dict[str, Any]


def write_hps_header(
    *,
    handle: TextIO,
    model_label: str,
    profile: HpsIesComparatorProfile,
    ies_path: Path,
    ies_spd_path: Path,
    eff_scale: float,
    lumens_per_lamp: float,
    total_luminaire_lumens: float,
    input_watts: float,
    zero_opening: bool,
    source_meta: JsonObject,
    pre_norm_fixture_ppf: float,
    fixture_anchor_ppf: float,
    ies_scale: float,
) -> None:
    handle.write(
        f"# {model_label} | profile={profile.profile_version} | variant={profile.variant} | derate(EFF_SCALE)={eff_scale:.4f}\n"
        f"# IES={ies_path.name} | SPD={ies_spd_path.name} | lumens_per_lamp={lumens_per_lamp:.1f} lm | "
        f"total_luminaire_lumens={total_luminaire_lumens:.1f} lm | input_watts={input_watts:.3f}\n"
        f"# zero_opening={int(zero_opening)} | source_geometry={source_meta['source_geometry_mode']} | "
        f"correction={source_meta['source_correction_function']} | "
        f"source_primitive={source_meta['source_primitive_type']} x{source_meta['source_primitive_count']}\n"
        f"# source_dims={source_meta['aperture_length_m'] / IN2M:.2f}in x "
        f"{source_meta['aperture_width_m'] / IN2M:.2f}in @ "
        f"{source_meta['aperture_z_m'] / IN2M:.2f}in\n"
        f"# pre_norm_fixture_ppf={pre_norm_fixture_ppf:.6f} umol/s | "
        f"fixture_anchor={fixture_anchor_ppf:.6f} umol/s | scale={ies_scale:.9f}\n"
        f"# bare_lamp_reference={BARE_LAMP_INITIAL_PPF_UMOL_S:.1f} umol/s @ "
        f"{BARE_LAMP_TOTAL_LUMINOUS_FLUX_LM:.1f} lm\n"
        "# whole-fixture interpretation: exactly one active IES emitter per fixture center\n"
        "# retained reference geometry is JSON-only; no synthetic arc tube or emitting reflector panels remain active\n\n"
    )


def write_hps_rad(
    *,
    out: Path,
    layout: JsonObject,
    model_label: str,
    profile: HpsIesComparatorProfile,
    ies_path: Path,
    ies_spd_path: Path,
    eff_scale: float,
    lumens_per_lamp: float,
    total_luminaire_lumens: float,
    input_watts: float,
    zero_opening: bool,
    source_meta: JsonObject,
    pre_norm_fixture_ppf: float,
    fixture_anchor_ppf: float,
    ies_scale: float,
    ies_rad_rel: str,
    ies_rot_x_deg: float,
    ies_rot_z_deg: float,
) -> None:
    with out.open("w", encoding="utf-8") as handle:
        write_hps_header(
            handle=handle,
            model_label=model_label,
            profile=profile,
            ies_path=ies_path,
            ies_spd_path=ies_spd_path,
            eff_scale=eff_scale,
            lumens_per_lamp=lumens_per_lamp,
            total_luminaire_lumens=total_luminaire_lumens,
            input_watts=input_watts,
            zero_opening=zero_opening,
            source_meta=source_meta,
            pre_norm_fixture_ppf=pre_norm_fixture_ppf,
            fixture_anchor_ppf=fixture_anchor_ppf,
            ies_scale=ies_scale,
        )
        for fixture in layout["fixtures"]:
            cx = float(fixture["cx"])
            cy = float(fixture["cy"])
            handle.write(
                f"!xform -rx {ies_rot_x_deg:.6f} -rz {ies_rot_z_deg:.6f} "
                f"-t {cx:.6f} {cy:.6f} {float(layout['z']):.6f} {ies_rad_rel}\n"
            )
