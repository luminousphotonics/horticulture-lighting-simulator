from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.engine.emitters.hps_generation.profile import IN2M, PROFILE_LABEL

JsonObject = dict[str, Any]


def summary_float(summary_lines: JsonObject, key: str) -> float:
    return float(summary_lines.get(key, 0.0) or 0.0)


def summary_int(summary_lines: JsonObject, key: str) -> int:
    return int(summary_lines.get(key, 0) or 0)


def summary_text(summary_lines: JsonObject, key: str) -> str:
    return str(summary_lines.get(key, "") or "")


def hps_base_summary_text(summary_lines: JsonObject, layout: JsonObject) -> list[str]:
    model_mode = str(summary_lines.get("model_mode", "") or "")
    return [
        str(summary_lines.get("model_label", PROFILE_LABEL)),
        f"mode={model_mode}",
        f"profile={summary_text(summary_lines, 'profile')}",
        f"archetype={summary_text(summary_lines, 'archetype')}",
        f"fixtures={summary_lines.get('fixtures', 0)} (NX={layout.get('nx', 0)}, NY={layout.get('ny', 0)})",
        f"coverage_ft={summary_float(summary_lines, 'coverage_ft'):.1f}",
        f"fixture_ppf={summary_float(summary_lines, 'fixture_ppf'):.6f} umol/s",
        f"active_fixture_ppf_umol_s={summary_float(summary_lines, 'active_fixture_ppf_umol_s'):.6f}",
        f"fixture_input_w={summary_float(summary_lines, 'fixture_input_w'):.6f} W",
        f"fixture_ppe={summary_float(summary_lines, 'fixture_ppe'):.6f} umol/J",
        f"total_ppf={summary_float(summary_lines, 'total_ppf'):.6f} umol/s",
        f"total_w={summary_float(summary_lines, 'total_w'):.6f} W",
        f"fixture_count={summary_int(summary_lines, 'fixture_count')}",
    ]


def hps_ies_summary_text(summary_lines: JsonObject) -> list[str]:
    lines = [
        f"ies_variant={summary_text(summary_lines, 'ies_variant')}",
        f"ies_file={summary_text(summary_lines, 'ies_file')}",
        f"ies_lumens_per_lamp_lm={summary_float(summary_lines, 'ies_lumens_per_lamp_lm'):.6f}",
        f"ies_total_luminaire_lumens_lm={summary_float(summary_lines, 'ies_total_luminaire_lumens_lm'):.6f}",
        f"ies_input_watts={summary_float(summary_lines, 'ies_input_watts'):.6f}",
        f"spd_umol_per_lumen={summary_float(summary_lines, 'spd_umol_per_lumen'):.9f}",
        f"pre_normalization_fixture_ppf_umol_s={summary_float(summary_lines, 'pre_normalization_fixture_ppf_umol_s'):.6f}",
        f"default_fixture_anchor_umol_s={summary_float(summary_lines, 'default_fixture_anchor_umol_s'):.6f}",
        f"fixture_anchor_authority={summary_text(summary_lines, 'fixture_anchor_authority')}",
        f"final_scale_multiplier={summary_float(summary_lines, 'final_scale_multiplier'):.9f}",
        f"source_correction_function={summary_text(summary_lines, 'source_correction_function')}",
        f"source_primitive_type={summary_text(summary_lines, 'source_primitive_type')}",
        f"source_primitive_count={summary_int(summary_lines, 'source_primitive_count')}",
        f"source_geometry_mode={summary_text(summary_lines, 'source_geometry_mode')}",
        f"effective_aperture_length_m={summary_float(summary_lines, 'effective_aperture_length_m'):.6f}",
        f"effective_aperture_width_m={summary_float(summary_lines, 'effective_aperture_width_m'):.6f}",
        f"effective_aperture_z_offset_m={summary_float(summary_lines, 'effective_aperture_z_offset_m'):.6f}",
    ]
    if summary_lines.get("anchor_notes"):
        lines.append(f"anchor_notes={summary_lines['anchor_notes']}")
    return lines


def hps_summary_text(summary_lines: JsonObject, layout: JsonObject) -> list[str]:
    lines = hps_base_summary_text(summary_lines, layout)
    model_mode = str(summary_lines.get("model_mode", "") or "")
    if model_mode == "whole_fixture_ies_comparator":
        lines.extend(hps_ies_summary_text(summary_lines))
    return lines


def write_summary_files(
    *,
    out_dir: Path,
    summary_lines: JsonObject,
    layout: JsonObject,
) -> None:
    summary_text_lines = hps_summary_text(summary_lines, layout)
    (out_dir / "hps_summary.txt").write_text(
        "\n".join(summary_text_lines) + "\n", encoding="utf-8"
    )
    (out_dir / "hps_power.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in summary_lines.items()) + "\n",
        encoding="utf-8",
    )
    (out_dir / "hps_layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")


def completion_lines(
    *,
    out: Path,
    out_dir: Path,
    model_label: str,
    layout: JsonObject,
    ies_path: Path,
    ies_spd_path: Path,
) -> tuple[str, ...]:
    aperture = layout["ies_source_aperture"]
    return (
        f"✔ Wrote {out}",
        f"✔ Wrote {out_dir / 'hps_summary.txt'}",
        f"✔ Wrote {out_dir / 'hps_power.txt'}",
        f"✔ Wrote {out_dir / 'hps_layout.json'}",
        f"{model_label}: one active fixture-level IES emitter per luminaire using "
        f"{ies_path.name} + {ies_spd_path.name}",
        "Active Radiance source: native ies2rad "
        f"{layout.get('ies_source_primitive_type', 'primitive')} with "
        f"{layout.get('ies_source_correction_function', 'unknown')} "
        f"({float(aperture['length_m']) / IN2M:.2f} x "
        f"{float(aperture['width_m']) / IN2M:.2f} in)",
    )
