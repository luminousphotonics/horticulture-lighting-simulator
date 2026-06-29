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


def normalize_ies_light_rgb_to_grey_scalar_carrier(rad_path: Path) -> bool:
    """Convert ies2rad light RGB to the scalar PAR PPFD carrier.

    HPS IES files may emit colored Radiance light channels. Baseline PPFD traces
    use R=G=B as a scalar PAR carrier, so keep the previous arithmetic-average
    decode value while making the source channels explicitly grey.
    """

    lines = rad_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_light = False
    changed = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        tokens = stripped.split()
        if len(tokens) >= 3 and tokens[1] in {"light", "illum"}:
            in_light = True
            out.append(line)
            continue
        if in_light and tokens and tokens[0] == "3" and len(tokens) >= 4:
            try:
                red = float(tokens[1])
                green = float(tokens[2])
                blue = float(tokens[3])
            except ValueError:
                out.append(line)
            else:
                grey = (red + green + blue) / 3.0
                grey_line = f"3 {grey:.6f} {grey:.6f} {grey:.6f}"
                out.append(grey_line)
                changed = changed or grey_line != line
            in_light = False
            continue
        if in_light and tokens and tokens[0] not in {"0", "3"}:
            in_light = False
        out.append(line)
    if changed:
        rad_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


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
        "# baseline_source_channel_policy=R=G=B scalar PAR PPFD carrier; colored ies2rad light RGB is grey-normalized by arithmetic mean\n"
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
