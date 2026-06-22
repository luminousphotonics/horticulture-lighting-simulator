#!/usr/bin/env python3
"""ppfd_metrics.py

Centralized PPFD field metrics.

The old "uniformity" log line (std/CV/DOU) is useful, but it does *not*
capture the practical control problem you highlighted:

If the canopy has a maximum photosynthetic setpoint (a hard PPFD ceiling),
the operator must dim globally until the *peak* is at or below that ceiling.
What matters then is how much average PPFD (and therefore total usable photons)
is still delivered after that peak-capping dim.

This module exposes:
  - "cap / usable photons" metrics (default)
  - legacy std/CV/DOU metrics (optional)

All inputs are in µmol/m^2/s.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Literal, overload

import numpy as np
from numpy.typing import NDArray


PPFDSamples = NDArray[np.float64]
ScalarMetricKey = Literal[
    "mean",
    "min",
    "max",
    "p05",
    "p50",
    "p95",
    "peak_over_mean",
    "min_over_mean",
    "min_over_max",
    "mean_over_peak",
    "ppf_out",
    "watts_in",
    "ppf_emitted",
    "capture_frac",
    "full_run_deuc_elec",
    "setpoint_ppfd",
    "cap_scale",
    "mean_at_cap",
    "min_at_cap",
    "p05_at_cap",
    "utilization_at_cap",
    "dim_penalty",
    "ppf_at_cap",
    "watts_at_cap",
    "capped_deuc_elec",
    "deuc_elec",
    "deuc",
    "cov_pm_5pct_mean_cap",
    "cov_pm_10pct_mean_cap",
    "under_5pct_mean_cap",
    "over_5pct_mean_cap",
    "score_hmean_5",
]
PpfdMetricValue = float | str | dict[str, float]
EPS = 1e-12


class PpfdMetrics(dict[str, PpfdMetricValue]):
    """PPFD metrics payload with scalar metric reads typed by key."""

    @overload
    def __getitem__(self, key: Literal["legacy"]) -> dict[str, float]: ...

    @overload
    def __getitem__(self, key: ScalarMetricKey) -> float: ...

    @overload
    def __getitem__(self, key: str) -> PpfdMetricValue: ...

    def __getitem__(self, key: str) -> PpfdMetricValue:
        return super().__getitem__(key)


@dataclass(frozen=True)
class PpfdFieldSpec:
    """Finite non-negative PPFD samples in µmol/m²/s, flattened to 1-D float64."""

    values: PPFDSamples
    units: str = "umol/m^2/s"
    coordinate_convention: str = (
        "sample order is caller-defined; metrics are spatially order-independent"
    )


def _as_finite_non_negative_1d(samples: PPFDSamples) -> PpfdFieldSpec:
    values = np.asarray(samples, dtype=np.float64).ravel()
    if values.size == 0:
        raise ValueError("ppfd array is empty")
    if not np.all(np.isfinite(values)):
        raise ValueError("ppfd array must contain only finite samples")
    if np.any(values < 0.0):
        raise ValueError("ppfd array must contain only non-negative samples")
    return PpfdFieldSpec(values=values)


def _base_metrics(p: PPFDSamples) -> PpfdMetrics:
    mean = float(np.mean(p))
    pmin = float(np.min(p))
    pmax = float(np.max(p))
    p05 = float(np.percentile(p, 5))
    p50 = float(np.percentile(p, 50))
    p95 = float(np.percentile(p, 95))
    return PpfdMetrics(
        {
            "mean": mean,
            "min": pmin,
            "max": pmax,
            "p05": p05,
            "p50": p50,
            "p95": p95,
            "peak_over_mean": float(pmax / max(EPS, mean)),
            "min_over_mean": float(pmin / max(EPS, mean)),
            "min_over_max": float(pmin / max(EPS, pmax)),
            "mean_over_peak": float(mean / max(EPS, pmax)),
        }
    )


def _add_power_metrics(
    out: PpfdMetrics,
    *,
    mean: float,
    canopy_area_m2: float | None,
    total_input_watts: float | None,
    emitted_ppf_umol_s: float | None,
) -> None:
    if canopy_area_m2 is not None and float(canopy_area_m2) > 0:
        area_m2 = float(canopy_area_m2)
        out["ppf_out"] = mean * area_m2
    if total_input_watts is not None:
        watts_in = float(total_input_watts)
        if watts_in > 0:
            out["watts_in"] = watts_in
    if emitted_ppf_umol_s is not None:
        emitted = float(emitted_ppf_umol_s)
        if emitted > 0:
            out["ppf_emitted"] = emitted
            if "ppf_out" in out:
                out["capture_frac"] = metric_float(out, "ppf_out") / emitted
    if "ppf_out" in out and "watts_in" in out:
        watts_in = metric_float(out, "watts_in")
        if watts_in > 0:
            out["full_run_deuc_elec"] = metric_float(out, "ppf_out") / watts_in


def _add_cap_power_metrics(
    out: PpfdMetrics,
    *,
    mean_at_cap: float,
    cap_scale: float,
    canopy_area_m2: float | None,
) -> None:
    if "ppf_out" not in out:
        return
    if canopy_area_m2 is None:
        raise ValueError("canopy_area_m2 is required when ppf_out is present")
    area_m2 = float(canopy_area_m2)
    out["ppf_at_cap"] = mean_at_cap * area_m2
    if "watts_in" not in out:
        return
    watts_at_cap = metric_float(out, "watts_in") * cap_scale
    out["watts_at_cap"] = watts_at_cap
    if watts_at_cap > 0:
        capped_deuc = metric_float(out, "ppf_at_cap") / watts_at_cap
        out["capped_deuc_elec"] = capped_deuc
        out["deuc_elec"] = capped_deuc
        out["deuc"] = capped_deuc


def _add_cap_coverage_metrics(
    out: PpfdMetrics, *, p_cap: PPFDSamples, mean_at_cap: float, setpoint: float
) -> None:
    if int(p_cap.size) <= 0:
        return
    lo_5 = 0.95 * mean_at_cap
    hi_5 = 1.05 * mean_at_cap
    lo_10 = 0.90 * mean_at_cap
    hi_10 = 1.10 * mean_at_cap
    within_5 = np.logical_and(p_cap >= lo_5, p_cap <= hi_5)
    within_10 = np.logical_and(p_cap >= lo_10, p_cap <= hi_10)
    under_5 = p_cap < lo_5
    over_5 = p_cap > hi_5
    cov_pm_5 = float(100.0 * np.mean(within_5))
    cov_pm_10 = float(100.0 * np.mean(within_10))
    under_5_pct = float(100.0 * np.mean(under_5))
    over_5_pct = float(100.0 * np.mean(over_5))
    util_cap = float(100.0 * (mean_at_cap / max(EPS, setpoint)))
    denom = util_cap + cov_pm_5
    score_hmean_5 = float(2.0 * util_cap * cov_pm_5 / denom) if denom > 0 else 0.0
    out.update(
        {
            "cov_pm_5pct_mean_cap": cov_pm_5,
            "cov_pm_10pct_mean_cap": cov_pm_10,
            "under_5pct_mean_cap": under_5_pct,
            "over_5pct_mean_cap": over_5_pct,
            "score_hmean_5": score_hmean_5,
        }
    )


def _add_in_spec_metrics(
    out: PpfdMetrics,
    *,
    p_cap: PPFDSamples,
    setpoint: float,
    canopy_area_m2: float | None,
    coverage_fracs: tuple[float, ...],
) -> None:
    if canopy_area_m2 is None or float(canopy_area_m2) <= 0:
        return
    area_m2 = float(canopy_area_m2)
    for frac in coverage_fracs:
        if frac <= 0:
            continue
        thr = frac * setpoint
        mask = p_cap >= thr
        cov = float(np.mean(mask))
        if cov > 0:
            mean_in_spec = float(np.mean(p_cap[mask]))
            ppf_in_spec = mean_in_spec * (area_m2 * cov)
        else:
            ppf_in_spec = 0.0
        out[f"ppf_ge_{int(round(frac * 100))}_at_cap"] = ppf_in_spec
        if "watts_at_cap" in out:
            watts_at_cap = metric_float(out, "watts_at_cap")
            if watts_at_cap > 0:
                out[f"deuc_ge_{int(round(frac * 100))}_at_cap"] = (
                    ppf_in_spec / watts_at_cap
                )


def _add_cap_metrics(
    out: PpfdMetrics,
    *,
    p: PPFDSamples,
    setpoint: float,
    canopy_area_m2: float | None,
    coverage_fracs: tuple[float, ...],
) -> None:
    pmax = metric_float(out, "max")
    cap_scale = min(1.0, setpoint / max(EPS, pmax))
    p_cap = p * cap_scale
    mean_at_cap = float(np.mean(p_cap))
    min_at_cap = float(np.min(p_cap))
    p05_at_cap = float(np.percentile(p_cap, 5))
    out.update(
        {
            "setpoint_ppfd": setpoint,
            "cap_scale": cap_scale,
            "dim_penalty": float(1.0 - cap_scale),
            "mean_at_cap": mean_at_cap,
            "min_at_cap": min_at_cap,
            "p05_at_cap": p05_at_cap,
            "utilization_at_cap": float(mean_at_cap / setpoint),
        }
    )
    _add_cap_power_metrics(
        out,
        mean_at_cap=mean_at_cap,
        cap_scale=cap_scale,
        canopy_area_m2=canopy_area_m2,
    )
    _add_cap_coverage_metrics(
        out, p_cap=p_cap, mean_at_cap=mean_at_cap, setpoint=setpoint
    )
    _add_in_spec_metrics(
        out,
        p_cap=p_cap,
        setpoint=setpoint,
        canopy_area_m2=canopy_area_m2,
        coverage_fracs=coverage_fracs,
    )


def _add_legacy_metrics(out: PpfdMetrics, *, p: PPFDSamples) -> None:
    mean = metric_float(out, "mean")
    pmin = metric_float(out, "min")
    pmax = metric_float(out, "max")
    std = float(np.std(p, ddof=0))
    cv = float(100.0 * std / max(EPS, mean))
    rmse = std
    mad = float(np.mean(np.abs(p - mean)))
    dou = float(100.0 * (1.0 - rmse / max(EPS, mean)))
    out["legacy"] = {
        "std": std,
        "cv_percent": cv,
        "dou_percent": dou,
        "rmse": rmse,
        "mad": mad,
        "min_over_avg": float(pmin / max(EPS, mean)),
        "min_over_max": float(pmin / max(EPS, pmax)),
    }


def compute_ppfd_metrics(
    ppfd: PPFDSamples,
    *,
    setpoint_ppfd: float | None = None,
    canopy_area_m2: float | None = None,
    total_input_watts: float | None = None,
    emitted_ppf_umol_s: float | None = None,
    legacy_metrics: bool = False,
    coverage_fracs: tuple[float, ...] = (0.90, 0.95),
) -> PpfdMetrics:
    """Compute metrics for a PPFD field.

    Args:
        ppfd: array of PPFD samples (µmol/m²/s).
        setpoint_ppfd: optional canopy PPFD ceiling/setpoint (µmol/m²/s).
            If provided, we compute the global dim factor needed so max<=setpoint,
            and report the average PPFD remaining under that constraint.
        legacy_metrics: if True, include std/CV/DOU/RMSE-like metrics.
    Returns:
        dict with fields:
          mean, min, max, p05, p50, p95
          peak_over_mean, min_over_mean, min_over_max, mean_over_peak
          (if total_input_watts is set)
            watts_in, full_run_deuc_elec
          (if emitted_ppf_umol_s is set)
            ppf_emitted, capture_frac
          (if setpoint_ppfd is set)
            setpoint_ppfd, cap_scale, mean_at_cap, min_at_cap, p05_at_cap,
            utilization_at_cap (mean_at_cap/setpoint), dim_penalty,
            cov_pm_5pct_mean_cap, cov_pm_10pct_mean_cap,
            under_5pct_mean_cap, over_5pct_mean_cap, score_hmean_5
          (if both setpoint_ppfd and total_input_watts are set, and canopy_area_m2 is set)
            watts_at_cap, capped_deuc_elec
          (compatibility aliases)
            deuc_elec, deuc -> capped_deuc_elec
          (if setpoint_ppfd and canopy_area_m2 are set)
            ppf_ge_{int(frac*100)}_at_cap (PPF from points >= frac*setpoint, after peak-capping)
          (if setpoint_ppfd and canopy_area_m2 and total_input_watts are set)
            deuc_ge_{int(frac*100)}_at_cap (efficacy of those "in-spec" photons, µmol/J)
          (if legacy_metrics)
            legacy: {std, cv_percent, dou_percent, rmse, mad, ...}
    """

    p = _as_finite_non_negative_1d(ppfd).values
    out = _base_metrics(p)
    _add_power_metrics(
        out,
        mean=metric_float(out, "mean"),
        canopy_area_m2=canopy_area_m2,
        total_input_watts=total_input_watts,
        emitted_ppf_umol_s=emitted_ppf_umol_s,
    )
    if setpoint_ppfd is not None and float(setpoint_ppfd) > 0:
        _add_cap_metrics(
            out,
            p=p,
            setpoint=float(setpoint_ppfd),
            canopy_area_m2=canopy_area_m2,
            coverage_fracs=coverage_fracs,
        )
    if legacy_metrics:
        _add_legacy_metrics(out, p=p)
    return out


def metric_float(metrics: Mapping[str, PpfdMetricValue], name: str) -> float:
    """Return a scalar metric after validating the metric payload shape."""

    value = metrics[name]
    if not isinstance(value, float):
        raise TypeError(f"metric {name!r} must be a float")
    return value


def _metric_mapping(
    metrics: Mapping[str, PpfdMetricValue], name: str
) -> Mapping[str, float] | None:
    value = metrics.get(name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"metric {name!r} must be a mapping")
    return value


def _cap_parts(m: Mapping[str, PpfdMetricValue]) -> list[str]:
    parts = [
        f"cap={metric_float(m, 'setpoint_ppfd'):.0f}",
        f"cap_scale={metric_float(m, 'cap_scale'):.3f}",
        f"mean@cap={metric_float(m, 'mean_at_cap'):.2f}",
        f"util@cap={100.0 * metric_float(m, 'utilization_at_cap'):.1f}%",
    ]
    if "ppf_at_cap" in m:
        parts.append(f"ppf@cap={metric_float(m, 'ppf_at_cap'):.1f} umol/s")
    if "capped_deuc_elec" in m:
        parts.append(f"DEUC_elec(cap)={metric_float(m, 'capped_deuc_elec'):.3f} umol/J")
    elif "deuc_elec" in m:
        parts.append(f"DEUC_elec(cap)={metric_float(m, 'deuc_elec'):.3f} umol/J")
    if "cov_pm_5pct_mean_cap" in m:
        parts.append(f"cov±5%mean={metric_float(m, 'cov_pm_5pct_mean_cap'):.1f}%")
    if "score_hmean_5" in m:
        parts.append(f"score_hmean_5={metric_float(m, 'score_hmean_5'):.1f}")
    return parts


def _efficacy_parts(m: Mapping[str, PpfdMetricValue]) -> list[str]:
    parts: list[str] = []
    if "full_run_deuc_elec" in m and "ppf_out" in m and "watts_in" in m:
        parts.append(
            f"full={metric_float(m, 'full_run_deuc_elec'):.3f} umol/J"
            f" ({metric_float(m, 'ppf_out'):.1f}/"
            f"{metric_float(m, 'watts_in'):.1f})"
        )
    if "capped_deuc_elec" in m and "ppf_at_cap" in m and "watts_at_cap" in m:
        parts.append(
            f"cap={metric_float(m, 'capped_deuc_elec'):.3f} umol/J"
            f" ({metric_float(m, 'ppf_at_cap'):.1f}/"
            f"{metric_float(m, 'watts_at_cap'):.1f})"
        )
    return parts


def format_ppfd_metrics_line(m: Mapping[str, PpfdMetricValue]) -> str:
    """Human-readable multi-line block for logs."""

    lines: list[str] = []
    lines.append(
        "stats: "
        f"mean={metric_float(m, 'mean'):.2f} "
        f"min={metric_float(m, 'min'):.2f} "
        f"max={metric_float(m, 'max'):.2f} "
        f"p05={metric_float(m, 'p05'):.2f} "
        f"p95={metric_float(m, 'p95'):.2f}"
    )
    lines.append(
        "ratios: "
        f"peak/mean={metric_float(m, 'peak_over_mean'):.3f} "
        f"min/mean={metric_float(m, 'min_over_mean'):.3f} "
        f"min/max={metric_float(m, 'min_over_max'):.3f}"
    )

    if "ppf_out" in m:
        lines.append(f"ppf: out={metric_float(m, 'ppf_out'):.1f} umol/s")

    if "setpoint_ppfd" in m:
        lines.append("cap: " + " ".join(_cap_parts(m)))

    efficacy_parts = _efficacy_parts(m)
    if efficacy_parts:
        lines.append("efficacy: " + " ".join(efficacy_parts))

    # Legacy is optional; only show if caller asked for it.
    legacy = _metric_mapping(m, "legacy")
    if legacy is not None:
        lines.append(
            "compat: "
            f"std={legacy['std']:.2f} CV={legacy['cv_percent']:.2f}% "
            f"DOU={legacy['dou_percent']:.2f}%"
        )

    return "\n".join(lines)
