#!/usr/bin/env python3
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SMD_SOURCE_VARIANT = "native"
ALTERED_SMD_SOURCE_VARIANT = "altered_cosine_140deg"
NARROW_SMD_SOURCE_VARIANT = "altered_cosine_100deg"


@dataclass(frozen=True)
class SmdAngularVariant:
    key: str
    short_label: str
    output_slug: str
    description: str
    rationale: str
    cal_function_name: str | None = None
    cal_expression: str | None = None

    @property
    def is_native(self) -> bool:
        return self.key == DEFAULT_SMD_SOURCE_VARIANT


def _m_from_fwhm(deg: float) -> float:
    half_angle = math.radians(0.5 * float(deg))
    c = max(math.cos(half_angle), 1e-9)
    return math.log(0.5) / math.log(c)


def _avg_sym_cos(m: float) -> float:
    return 2.0 / (m + 2.0)


def normalize_smd_source_variant(raw: str | None) -> str:
    token = str(raw or DEFAULT_SMD_SOURCE_VARIANT).strip().lower()
    if token in {"", "native", "baseline", "default"}:
        return DEFAULT_SMD_SOURCE_VARIANT
    if token in {
        "altered_cosine_100deg",
        "cosine_100deg",
        "narrow_cosine_100deg",
    }:
        return NARROW_SMD_SOURCE_VARIANT
    if token in {
        "altered",
        "alt",
        "reviewer_alt",
        "reviewer_alt1",
        "altered_cosine_140deg",
        "cosine_140deg",
        "wide_cosine_140deg",
    }:
        return ALTERED_SMD_SOURCE_VARIANT
    raise ValueError(f"Unsupported SMD source variant {raw!r}.")


def get_smd_source_variant(raw: str | None) -> SmdAngularVariant:
    key = normalize_smd_source_variant(raw)
    if key == DEFAULT_SMD_SOURCE_VARIANT:
        return SmdAngularVariant(
            key=DEFAULT_SMD_SOURCE_VARIANT,
            short_label="Native SMD surrogate",
            output_slug="native",
            description="Current native SMD surrogate with the shared PMMA/PTFE stack and no extra angular wrapper.",
            rationale="Baseline proposed-system module surrogate.",
        )
    if key == ALTERED_SMD_SOURCE_VARIANT:
        m = _m_from_fwhm(140.0)
        gain = 1.0 / max(_avg_sym_cos(m), 1e-12)
        return SmdAngularVariant(
            key=ALTERED_SMD_SOURCE_VARIANT,
            short_label="Altered SMD surrogate",
            output_slug="altered",
            description=(
                "Flux-normalized symmetric cosine-power source modifier with a 140 degree FWHM "
                "applied ahead of the same PMMA/PTFE stack."
            ),
            rationale=(
                "Reviewer-facing alternate proposed-source case representing a plausibly more "
                "diffuse module emission family while preserving total source photons."
            ),
            cal_function_name="smd_source_variant",
            cal_expression=f"{gain:.9f} * pwr(c, {m:.9f})",
        )
    if key == NARROW_SMD_SOURCE_VARIANT:
        m = _m_from_fwhm(100.0)
        gain = 1.0 / max(_avg_sym_cos(m), 1e-12)
        return SmdAngularVariant(
            key=NARROW_SMD_SOURCE_VARIANT,
            short_label="Altered SMD surrogate (100 degree FWHM)",
            output_slug="altered_100deg",
            description=(
                "Flux-normalized symmetric cosine-power source modifier with a 100 degree FWHM "
                "applied ahead of the same PMMA/PTFE stack."
            ),
            rationale=(
                "Reviewer-facing narrower proposed-source bracket case preserving total source photons "
                "while shifting emission toward a tighter downward distribution."
            ),
            cal_function_name="smd_source_variant",
            cal_expression=f"{gain:.9f} * pwr(c, {m:.9f})",
        )
    raise ValueError(f"Unsupported SMD source variant {raw!r}.")


def write_smd_source_variant_cal(path: Path, variant: SmdAngularVariant) -> Path | None:
    if variant.is_native:
        return None
    if not variant.cal_function_name or not variant.cal_expression:
        raise ValueError(f"Variant {variant.key!r} is missing Radiance CAL data.")
    text = (
        "{ smd_source_variant.cal }\n"
        "eps = 1e-9;\n"
        "c = abs(Dz);\n"
        "pwr(x,y) = exp(y*log(max(x,1e-9)));\n"
        f"{variant.cal_function_name} = {variant.cal_expression};\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
