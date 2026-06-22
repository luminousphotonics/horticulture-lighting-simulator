from __future__ import annotations

import csv
import hashlib
import json
import os
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, SupportsFloat, SupportsIndex, cast

from rad_rebuild.radiance.engine.photometry.ies_photon_toolkit import (
    RelativeSPDPhotonMetrics,
    spd_photon_metrics,
)
from rad_rebuild.radiance.paths import (
    RADIANCE_CURVE_DATA_ROOT,
    RADIANCE_DATA_ROOT,
    REPO_ROOT,
)


CURVE_ROOT = RADIANCE_CURVE_DATA_ROOT / "smd"
CURVE_MODEL_VERSION = "curve_vf_rel_ppe_v1"

DEFAULT_WHITE_PPE_CSV = CURVE_ROOT / "white_ppe_vs_fC.csv"
DEFAULT_WHITE_VF_CSV = CURVE_ROOT / "white_fC_vs_fV.csv"
DEFAULT_RED_PPE_CSV = CURVE_ROOT / "red_ppe_vs_fC.csv"
DEFAULT_RED_VF_CSV = CURVE_ROOT / "red_fC_vs_fV.csv"
DEFAULT_WW_SPD_CSV = CURVE_ROOT / "smd_3000k_spd.csv"
DEFAULT_CW_SPD_CSV = CURVE_ROOT / "smd_5000k_spd.csv"
DEFAULT_RED_SPD_CSV = CURVE_ROOT / "smd_660nm_spd.csv"
NumericSourceValue = str | bytes | SupportsFloat | SupportsIndex
CurveValidationValue = str | float
CurveValidationPayload = dict[str, CurveValidationValue]


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return float(default)
    return float(raw)


def _str_env(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _float_from_source(
    source: Mapping[str, object] | None, name: str, default: float
) -> float:
    if source is None:
        return _float_env(name, default)
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    if isinstance(raw, str | bytes):
        return float(raw)
    if hasattr(raw, "__float__") or hasattr(raw, "__index__"):
        narrowed = cast(NumericSourceValue, raw)
        return float(narrowed)
    raise TypeError(f"{name} must be numeric or numeric text")


def _str_from_source(
    source: Mapping[str, object] | None, name: str, default: str
) -> str:
    if source is None:
        return _str_env(name, default)
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip()


def _resolve_curve_path(env_name: str, default_path: Path) -> Path:
    raw = os.getenv(env_name, "").strip()
    if raw:
        return _resolve_curve_path_value(raw, default_path)
    return default_path.resolve()


def _resolve_curve_path_from_source(
    source: Mapping[str, object] | None,
    env_name: str,
    default_path: Path,
) -> Path:
    if source is None:
        return _resolve_curve_path(env_name, default_path)
    raw = str(source.get(env_name, "")).strip()
    if raw:
        return _resolve_curve_path_value(raw, default_path)
    return default_path.resolve()


def _resolve_curve_path_value(raw: str, default_path: Path) -> Path:
    raw_path = Path(raw).expanduser()
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(raw_path)
        candidates.append(REPO_ROOT / raw_path)
        candidates.append(RADIANCE_DATA_ROOT / raw_path)
        candidates.append(RADIANCE_CURVE_DATA_ROOT / raw_path)
        candidates.append(CURVE_ROOT / raw_path)

    parts = raw_path.parts
    if "curve_data" in parts:
        idx = parts.index("curve_data")
        rel = Path(*parts[idx:])
        candidates.append(RADIANCE_DATA_ROOT / rel)
        if len(parts) > idx + 1:
            candidates.append(RADIANCE_CURVE_DATA_ROOT / Path(*parts[idx + 1 :]))
    if "smd" in parts:
        idx = len(parts) - 1 - list(reversed(parts)).index("smd")
        if len(parts) > idx + 1:
            candidates.append(CURVE_ROOT / Path(*parts[idx + 1 :]))

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    # Older bundle manifests may carry absolute paths from the machine that
    # generated them. If those paths do not exist here, fall back to the
    # repo-local default curve file with the matching role.
    return default_path.resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LinearInterpCurve:
    x: tuple[float, ...]
    y: tuple[float, ...]
    source_path: str
    x_unit: str
    y_unit: str

    def __post_init__(self) -> None:
        if len(self.x) != len(self.y):
            raise ValueError("curve x/y lengths differ")
        if len(self.x) < 2:
            raise ValueError("curve must contain at least two points")
        prev = self.x[0]
        for cur in self.x[1:]:
            if cur < prev:
                raise ValueError("curve x values must be sorted ascending")
            prev = cur

    @property
    def x_min(self) -> float:
        return float(self.x[0])

    @property
    def x_max(self) -> float:
        return float(self.x[-1])

    def eval(self, value: float) -> float:
        if value <= self.x[0]:
            return float(self.y[0])
        if value >= self.x[-1]:
            return float(self.y[-1])
        idx = bisect_right(self.x, value) - 1
        x0 = float(self.x[idx])
        x1 = float(self.x[idx + 1])
        y0 = float(self.y[idx])
        y1 = float(self.y[idx + 1])
        if abs(x1 - x0) <= 1e-12:
            return y1
        t = (value - x0) / (x1 - x0)
        return y0 + (y1 - y0) * t


def _load_numeric_rows(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for raw in reader:
            if not raw:
                continue
            if len(raw) < 2:
                continue
            first = raw[0].strip()
            second = raw[1].strip()
            if not first or not second:
                continue
            try:
                a = float(first)
                b = float(second)
            except ValueError:
                # Allow an optional header row without guessing or smoothing.
                continue
            rows.append((a, b))
    if len(rows) < 2:
        raise ValueError(
            f"curve file {path} does not contain at least two numeric rows"
        )
    return rows


def _dedupe_sorted_pairs(
    pairs: Iterable[tuple[float, float]],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in sorted(pairs, key=lambda item: item[0]):
        if xs and abs(x - xs[-1]) <= 1e-12:
            ys[-1] = float(y)
            continue
        xs.append(float(x))
        ys.append(float(y))
    if len(xs) < 2:
        raise ValueError("curve collapses to fewer than two unique x values")
    return tuple(xs), tuple(ys)


def load_rel_ppe_curve(path: Path) -> LinearInterpCurve:
    # CSV columns: forward current (mA), relative PPE (%).
    pairs = [((i_ma / 1000.0), (pct / 100.0)) for i_ma, pct in _load_numeric_rows(path)]
    xs, ys = _dedupe_sorted_pairs(pairs)
    return LinearInterpCurve(xs, ys, str(path), "A", "multiplier")


def load_vf_curve(path: Path) -> LinearInterpCurve:
    # CSV columns: forward voltage (V), forward current (mA).
    # Invert to runtime Vf(I) by storing current in A on the x-axis.
    pairs = [((i_ma / 1000.0), vf_v) for vf_v, i_ma in _load_numeric_rows(path)]
    xs, ys = _dedupe_sorted_pairs(pairs)
    return LinearInterpCurve(xs, ys, str(path), "A", "V")


@lru_cache(maxsize=None)
def _load_spd_metrics(path_str: str) -> RelativeSPDPhotonMetrics:
    return spd_photon_metrics(Path(path_str))


def _build_channel_spectral_model(
    *,
    components: Iterable[tuple[str, int, float, float, Path]],
) -> "ChannelSpectralModel":
    entries: list[SpectralComponent] = []
    total_nominal_electrical_w = 0.0
    total_nominal_photon_umol_s = 0.0
    total_nominal_radiant_w = 0.0

    for label, count, nominal_package_w, nominal_ppe, spd_path in components:
        resolved = Path(spd_path).resolve()
        metrics = _load_spd_metrics(str(resolved))
        component = SpectralComponent(
            label=label,
            count=int(count),
            nominal_package_w=float(nominal_package_w),
            nominal_ppe_umol_per_j=float(nominal_ppe),
            spd_path=str(resolved),
            spd_metrics=metrics,
        )
        entries.append(component)
        nominal_electrical_w = component.count * component.nominal_package_w
        nominal_photon_umol_s = nominal_electrical_w * component.nominal_ppe_umol_per_j
        nominal_radiant_w = nominal_photon_umol_s / max(
            metrics.par_photon_umol_per_radiant_w, 1e-12
        )
        total_nominal_electrical_w += nominal_electrical_w
        total_nominal_photon_umol_s += nominal_photon_umol_s
        total_nominal_radiant_w += nominal_radiant_w

    if (
        total_nominal_electrical_w <= 0.0
        or total_nominal_photon_umol_s <= 0.0
        or total_nominal_radiant_w <= 0.0
    ):
        raise ValueError("channel spectral model produced non-positive nominal totals")

    return ChannelSpectralModel(
        par_photon_umol_per_radiant_w=total_nominal_photon_umol_s
        / total_nominal_radiant_w,
        nominal_radiant_efficiency=total_nominal_radiant_w / total_nominal_electrical_w,
        components=tuple(entries),
    )


def _solve_current_for_package_power(
    target_w: float, vf_curve: LinearInterpCurve
) -> float:
    if target_w <= 0.0:
        return 0.0
    hi = vf_curve.x_max
    max_power = hi * vf_curve.eval(hi)
    if target_w >= max_power:
        return hi
    lo = 0.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        power_mid = mid * vf_curve.eval(mid)
        if power_mid < target_w:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class SMDChannelModel:
    label: str
    count: int
    nominal_package_w: float
    nominal_ppe_umol_per_j: float
    vf_curve: LinearInterpCurve
    raw_rel_ppe_curve: LinearInterpCurve
    nominal_current_a: float
    nominal_vf_v: float
    nominal_rel_ppe_multiplier: float
    spectral_model: "ChannelSpectralModel"

    def raw_rel_ppe(self, current_a: float) -> float:
        if current_a <= 0.0:
            return self.raw_rel_ppe_curve.eval(0.0)
        return self.raw_rel_ppe_curve.eval(min(current_a, self.raw_rel_ppe_curve.x_max))

    def normalized_rel_ppe(self, current_a: float, reference_mode: str) -> float:
        raw = self.raw_rel_ppe(current_a)
        if reference_mode == "raw_csv":
            return raw
        denom = self.nominal_rel_ppe_multiplier
        if denom <= 1e-12:
            return raw
        return raw / denom

    def vf(self, current_a: float) -> float:
        if current_a <= 0.0:
            return self.vf_curve.eval(0.0)
        return self.vf_curve.eval(min(current_a, self.vf_curve.x_max))

    @property
    def max_current_a(self) -> float:
        return self.vf_curve.x_max


@dataclass(frozen=True)
class ChannelState:
    label: str
    count: int
    current_a: float
    vf_v: float
    rel_ppe_multiplier_raw: float
    rel_ppe_multiplier_norm: float
    package_power_w: float
    channel_power_w: float
    channel_radiant_w_raw: float
    nominal_ppe_umol_per_j: float
    channel_photon_umol_s_raw: float


@dataclass(frozen=True)
class SpectralComponent:
    label: str
    count: int
    nominal_package_w: float
    nominal_ppe_umol_per_j: float
    spd_path: str
    spd_metrics: RelativeSPDPhotonMetrics


@dataclass(frozen=True)
class ChannelSpectralModel:
    par_photon_umol_per_radiant_w: float
    nominal_radiant_efficiency: float
    components: tuple[SpectralComponent, ...]


@dataclass(frozen=True)
class ModuleState:
    input_w: float
    led_supply_w: float
    drive_ratio: float
    current_reference_mode: str
    thermal_multiplier: float
    effective_w_after_losses: float
    source_photon_umol_s: float
    output_photon_umol_s: float
    package_photon_umol_s_raw: float
    package_photon_umol_s_after_thermal: float
    wall_plug_ppe_umol_per_j: float
    source_ppe_umol_per_j: float
    output_ppe_umol_per_j: float
    channels: tuple[ChannelState, ...]


class SMDCurveModel:
    def __init__(
        self,
        *,
        driver_eff: float,
        wiring_eff: float,
        pmma_transmission: float,
        ppe_reference_mode: str,
        thermal_ref_input_w: float,
        thermal_ref_multiplier: float,
        thermal_slope_per_w: float,
        thermal_min_multiplier: float,
        thermal_max_multiplier: float,
        debug_enabled: bool,
        env: Mapping[str, object] | None = None,
    ) -> None:
        self.driver_eff = float(driver_eff)
        self.wiring_eff = float(wiring_eff)
        self.pmma_transmission = float(pmma_transmission)
        self.ppe_reference_mode = str(ppe_reference_mode)
        self.thermal_ref_input_w = float(thermal_ref_input_w)
        self.thermal_ref_multiplier = float(thermal_ref_multiplier)
        self.thermal_slope_per_w = float(thermal_slope_per_w)
        self.thermal_min_multiplier = float(thermal_min_multiplier)
        self.thermal_max_multiplier = float(thermal_max_multiplier)
        self.debug_enabled = bool(debug_enabled)

        self.white = _build_channel_model(
            label="white",
            count_envs=("SMD_WW_COUNT", "SMD_CW_COUNT"),
            count_default=52,
            nominal_w_envs=("SMD_WW_NOMINAL_W", "SMD_CW_NOMINAL_W"),
            nominal_w_default=0.68,
            nominal_ppe_envs=("SMD_WW_NOMINAL_PPE", "SMD_CW_NOMINAL_PPE"),
            nominal_ppe_default=(2.73, 2.81),
            vf_path=_resolve_curve_path_from_source(
                env, "SMD_WHITE_VF_CSV", DEFAULT_WHITE_VF_CSV
            ),
            ppe_path=_resolve_curve_path_from_source(
                env, "SMD_WHITE_PPE_CSV", DEFAULT_WHITE_PPE_CSV
            ),
            env=env,
        )
        self.red = _build_channel_model(
            label="red",
            count_envs=("SMD_RED_COUNT",),
            count_default=41,
            nominal_w_envs=("SMD_RED_NOMINAL_W",),
            nominal_w_default=0.44,
            nominal_ppe_envs=("SMD_RED_NOMINAL_PPE",),
            nominal_ppe_default=(4.13,),
            vf_path=_resolve_curve_path_from_source(
                env, "SMD_RED_VF_CSV", DEFAULT_RED_VF_CSV
            ),
            ppe_path=_resolve_curve_path_from_source(
                env, "SMD_RED_PPE_CSV", DEFAULT_RED_PPE_CSV
            ),
            env=env,
        )

        self.nominal_led_power_w = (
            self.white.nominal_package_w * self.white.count
            + self.red.nominal_package_w * self.red.count
        )
        wall_loss = max(self.driver_eff * self.wiring_eff, 1e-9)
        # Nominal package powers are LED-side electrical assumptions, so convert
        # back to wall-input watts for runtime reporting and validation.
        self.nominal_module_input_w = self.nominal_led_power_w / wall_loss
        self.nominal_drive_ratio = 1.0

    def thermal_multiplier(self, input_w: float) -> float:
        if input_w <= 0.0:
            return self.thermal_max_multiplier
        raw = self.thermal_ref_multiplier - self.thermal_slope_per_w * (
            float(input_w) - self.thermal_ref_input_w
        )
        return max(self.thermal_min_multiplier, min(self.thermal_max_multiplier, raw))

    def led_power_for_ratio(self, drive_ratio: float) -> tuple[float, float, float]:
        white_i = max(
            0.0,
            min(self.white.max_current_a, drive_ratio * self.white.nominal_current_a),
        )
        red_i = max(
            0.0, min(self.red.max_current_a, drive_ratio * self.red.nominal_current_a)
        )
        white_power = self.white.count * white_i * self.white.vf(white_i)
        red_power = self.red.count * red_i * self.red.vf(red_i)
        return white_power + red_power, white_i, red_i

    def solve_drive_ratio(self, led_supply_w: float) -> tuple[float, float, float]:
        if led_supply_w <= 0.0:
            return 0.0, 0.0, 0.0
        max_ratio = max(
            1.0,
            self.white.max_current_a / max(self.white.nominal_current_a, 1e-9),
            self.red.max_current_a / max(self.red.nominal_current_a, 1e-9),
        )
        max_power, max_white_i, max_red_i = self.led_power_for_ratio(max_ratio)
        if led_supply_w >= max_power:
            return max_ratio, max_white_i, max_red_i
        lo = 0.0
        hi = max_ratio
        white_i = 0.0
        red_i = 0.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            power_mid, white_i, red_i = self.led_power_for_ratio(mid)
            if power_mid < led_supply_w:
                lo = mid
            else:
                hi = mid
        drive = 0.5 * (lo + hi)
        _, white_i, red_i = self.led_power_for_ratio(drive)
        return drive, white_i, red_i

    def _channel_state(self, model: SMDChannelModel, current_a: float) -> ChannelState:
        vf_v = model.vf(current_a)
        raw_mult = model.raw_rel_ppe(current_a)
        norm_mult = model.normalized_rel_ppe(current_a, self.ppe_reference_mode)
        package_power_w = current_a * vf_v
        channel_power_w = package_power_w * model.count
        # The live PPFD path stays photon-first: SPD data only refines the
        # radiant-to-photon bookkeeping upstream, while nominal PPE anchors keep
        # the runtime source-photon authority numerically aligned with the
        # pre-SPD curve model.
        channel_radiant_w_raw = (
            channel_power_w * model.spectral_model.nominal_radiant_efficiency
        )
        channel_photon_umol_s_raw = (
            channel_radiant_w_raw
            * model.spectral_model.par_photon_umol_per_radiant_w
            * norm_mult
        )
        return ChannelState(
            label=model.label,
            count=model.count,
            current_a=current_a,
            vf_v=vf_v,
            rel_ppe_multiplier_raw=raw_mult,
            rel_ppe_multiplier_norm=norm_mult,
            package_power_w=package_power_w,
            channel_power_w=channel_power_w,
            channel_radiant_w_raw=channel_radiant_w_raw,
            nominal_ppe_umol_per_j=model.nominal_ppe_umol_per_j,
            channel_photon_umol_s_raw=channel_photon_umol_s_raw,
        )

    def evaluate_module(
        self, input_w: float, *, pmma_active: bool = True
    ) -> ModuleState:
        input_w = max(0.0, float(input_w))
        led_supply_w = input_w * self.driver_eff * self.wiring_eff
        drive_ratio, white_i, red_i = self.solve_drive_ratio(led_supply_w)
        white_state = self._channel_state(self.white, white_i)
        red_state = self._channel_state(self.red, red_i)
        channels = (white_state, red_state)
        package_photon_umol_s_raw = sum(ch.channel_photon_umol_s_raw for ch in channels)
        thermal_mult = self.thermal_multiplier(input_w)
        package_photon_umol_s_after_thermal = package_photon_umol_s_raw * thermal_mult
        source_photon_umol_s = package_photon_umol_s_after_thermal
        optical_transmission = self.pmma_transmission if pmma_active else 1.0
        output_photon_umol_s = source_photon_umol_s * optical_transmission
        effective_w_after_losses = (
            input_w * self.driver_eff * self.wiring_eff * thermal_mult
        )
        wall_plug_ppe = output_photon_umol_s / input_w if input_w > 1e-12 else 0.0
        source_ppe = source_photon_umol_s / input_w if input_w > 1e-12 else 0.0
        output_ppe = (
            output_photon_umol_s / max(input_w, 1e-12) if input_w > 1e-12 else 0.0
        )
        return ModuleState(
            input_w=input_w,
            led_supply_w=led_supply_w,
            drive_ratio=drive_ratio,
            current_reference_mode=self.ppe_reference_mode,
            thermal_multiplier=thermal_mult,
            effective_w_after_losses=effective_w_after_losses,
            source_photon_umol_s=source_photon_umol_s,
            output_photon_umol_s=output_photon_umol_s,
            package_photon_umol_s_raw=package_photon_umol_s_raw,
            package_photon_umol_s_after_thermal=package_photon_umol_s_after_thermal,
            wall_plug_ppe_umol_per_j=wall_plug_ppe,
            source_ppe_umol_per_j=source_ppe,
            output_ppe_umol_per_j=output_ppe,
            channels=channels,
        )


def _build_channel_model(
    *,
    label: str,
    count_envs: tuple[str, ...],
    count_default: int,
    nominal_w_envs: tuple[str, ...],
    nominal_w_default: float,
    nominal_ppe_envs: tuple[str, ...],
    nominal_ppe_default: tuple[float, ...],
    vf_path: Path,
    ppe_path: Path,
    env: Mapping[str, object] | None = None,
) -> SMDChannelModel:
    if label == "white":
        ww_count = int(_float_from_source(env, "SMD_WW_COUNT", float(count_default)))
        cw_count = int(_float_from_source(env, "SMD_CW_COUNT", float(count_default)))
        ww_nominal_w = _float_from_source(env, nominal_w_envs[0], nominal_w_default)
        cw_nominal_w = _float_from_source(env, nominal_w_envs[1], nominal_w_default)
        ww_nominal_ppe = _float_from_source(
            env, nominal_ppe_envs[0], nominal_ppe_default[0]
        )
        cw_nominal_ppe = _float_from_source(
            env, nominal_ppe_envs[1], nominal_ppe_default[1]
        )
        count = int(
            _float_from_source(env, "SMD_WHITE_COUNT_TOTAL", float(count_default * 2))
        )
        nominal_package_w = (ww_nominal_w + cw_nominal_w) / 2.0
        nominal_ppe = (ww_nominal_ppe + cw_nominal_ppe) / 2.0
        spectral_model = _build_channel_spectral_model(
            components=(
                (
                    "warm_white",
                    ww_count,
                    ww_nominal_w,
                    ww_nominal_ppe,
                    _resolve_curve_path_from_source(
                        env, "SMD_WW_SPD_CSV", DEFAULT_WW_SPD_CSV
                    ),
                ),
                (
                    "cool_white",
                    cw_count,
                    cw_nominal_w,
                    cw_nominal_ppe,
                    _resolve_curve_path_from_source(
                        env, "SMD_CW_SPD_CSV", DEFAULT_CW_SPD_CSV
                    ),
                ),
            )
        )
    else:
        count = int(_float_from_source(env, count_envs[0], float(count_default)))
        nominal_package_w = _float_from_source(
            env, nominal_w_envs[0], nominal_w_default
        )
        nominal_ppe = _float_from_source(
            env, nominal_ppe_envs[0], nominal_ppe_default[0]
        )
        spectral_model = _build_channel_spectral_model(
            components=(
                (
                    "deep_red",
                    count,
                    nominal_package_w,
                    nominal_ppe,
                    _resolve_curve_path_from_source(
                        env, "SMD_RED_SPD_CSV", DEFAULT_RED_SPD_CSV
                    ),
                ),
            )
        )

    vf_curve = load_vf_curve(vf_path)
    raw_rel_ppe_curve = load_rel_ppe_curve(ppe_path)
    nominal_current_a = _solve_current_for_package_power(nominal_package_w, vf_curve)
    nominal_vf_v = vf_curve.eval(nominal_current_a)
    nominal_rel_ppe_multiplier = raw_rel_ppe_curve.eval(nominal_current_a)
    return SMDChannelModel(
        label=label,
        count=count,
        nominal_package_w=nominal_package_w,
        nominal_ppe_umol_per_j=nominal_ppe,
        vf_curve=vf_curve,
        raw_rel_ppe_curve=raw_rel_ppe_curve,
        nominal_current_a=nominal_current_a,
        nominal_vf_v=nominal_vf_v,
        nominal_rel_ppe_multiplier=nominal_rel_ppe_multiplier,
        spectral_model=spectral_model,
    )


@lru_cache(maxsize=1)
def load_smd_curve_model() -> SMDCurveModel:
    return build_smd_curve_model()


def build_smd_curve_model(env: Mapping[str, object] | None = None) -> SMDCurveModel:
    driver_eff = _float_from_source(env, "DRIVER_EFF", 0.96)
    wiring_eff = _float_from_source(env, "WIRING_EFF", 0.99)
    pmma_t = _float_from_source(env, "PMMA_T", 0.92)
    ppe_reference_mode = _str_from_source(
        env, "SMD_PPE_REFERENCE_MODE", "nominal_current"
    ).lower()
    if ppe_reference_mode not in {"nominal_current", "raw_csv"}:
        raise ValueError(f"Unsupported SMD_PPE_REFERENCE_MODE={ppe_reference_mode!r}")

    nominal_led_power_w = (
        52.0 * _float_from_source(env, "SMD_WW_NOMINAL_W", 0.68)
        + 52.0 * _float_from_source(env, "SMD_CW_NOMINAL_W", 0.68)
        + 41.0 * _float_from_source(env, "SMD_RED_NOMINAL_W", 0.44)
    )
    nominal_module_input_w = nominal_led_power_w / max(driver_eff * wiring_eff, 1e-9)
    thermal_ref_input_w = _float_from_source(
        env, "SMD_THERMAL_REF_INPUT_W", nominal_module_input_w
    )
    thermal_ref_multiplier = _float_from_source(
        env, "SMD_THERMAL_REF_MULTIPLIER", 0.985
    )
    thermal_slope_per_w = _float_from_source(env, "SMD_THERMAL_SLOPE_PER_W", 0.0015)
    thermal_min_multiplier = _float_from_source(env, "SMD_THERMAL_MIN_MULTIPLIER", 0.90)
    thermal_max_multiplier = _float_from_source(env, "SMD_THERMAL_MAX_MULTIPLIER", 1.00)
    debug_enabled = _str_from_source(env, "SMD_CURVE_DEBUG", "0").lower() not in {
        "0",
        "false",
        "no",
        "off",
        "",
    }

    return SMDCurveModel(
        driver_eff=driver_eff,
        wiring_eff=wiring_eff,
        pmma_transmission=pmma_t,
        ppe_reference_mode=ppe_reference_mode,
        thermal_ref_input_w=thermal_ref_input_w,
        thermal_ref_multiplier=thermal_ref_multiplier,
        thermal_slope_per_w=thermal_slope_per_w,
        thermal_min_multiplier=thermal_min_multiplier,
        thermal_max_multiplier=thermal_max_multiplier,
        debug_enabled=debug_enabled,
        env=env,
    )


def curve_model_manifest(env: Mapping[str, object] | None = None) -> dict[str, object]:
    model = build_smd_curve_model(env=env)
    white_vf_path = Path(model.white.vf_curve.source_path)
    white_ppe_path = Path(model.white.raw_rel_ppe_curve.source_path)
    red_vf_path = Path(model.red.vf_curve.source_path)
    red_ppe_path = Path(model.red.raw_rel_ppe_curve.source_path)
    ww_spd_path = Path(model.white.spectral_model.components[0].spd_path)
    cw_spd_path = Path(model.white.spectral_model.components[1].spd_path)
    red_spd_path = Path(model.red.spectral_model.components[0].spd_path)
    return {
        "curve_model_version": CURVE_MODEL_VERSION,
        "enabled": _str_from_source(env, "SMD_MODEL", "curve").lower() != "legacy",
        "model": _str_from_source(env, "SMD_MODEL", "curve").lower(),
        "ppe_reference_mode": model.ppe_reference_mode,
        "curve_model_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "driver_eff": model.driver_eff,
        "wiring_eff": model.wiring_eff,
        "pmma_transmission": model.pmma_transmission,
        "thermal_ref_input_w": model.thermal_ref_input_w,
        "thermal_ref_multiplier": model.thermal_ref_multiplier,
        "thermal_slope_per_w": model.thermal_slope_per_w,
        "thermal_min_multiplier": model.thermal_min_multiplier,
        "thermal_max_multiplier": model.thermal_max_multiplier,
        "nominal_led_power_w": model.nominal_led_power_w,
        "nominal_module_input_w": model.nominal_module_input_w,
        "nominal_package_assumptions": {
            "warm_white": {
                "count": int(_float_from_source(env, "SMD_WW_COUNT", 52.0)),
                "nominal_package_w": _float_from_source(env, "SMD_WW_NOMINAL_W", 0.68),
                "nominal_ppe_umol_per_j": _float_from_source(
                    env, "SMD_WW_NOMINAL_PPE", 2.73
                ),
            },
            "cool_white": {
                "count": int(_float_from_source(env, "SMD_CW_COUNT", 52.0)),
                "nominal_package_w": _float_from_source(env, "SMD_CW_NOMINAL_W", 0.68),
                "nominal_ppe_umol_per_j": _float_from_source(
                    env, "SMD_CW_NOMINAL_PPE", 2.81
                ),
            },
            "deep_red": {
                "count": int(_float_from_source(env, "SMD_RED_COUNT", 41.0)),
                "nominal_package_w": _float_from_source(env, "SMD_RED_NOMINAL_W", 0.44),
                "nominal_ppe_umol_per_j": _float_from_source(
                    env, "SMD_RED_NOMINAL_PPE", 4.13
                ),
            },
        },
        "channels": {
            "white": {
                "count": model.white.count,
                "nominal_package_w": model.white.nominal_package_w,
                "nominal_ppe_umol_per_j": model.white.nominal_ppe_umol_per_j,
                "nominal_current_a": model.white.nominal_current_a,
                "nominal_vf_v": model.white.nominal_vf_v,
                "nominal_rel_ppe_multiplier_raw": model.white.nominal_rel_ppe_multiplier,
                "spectral_par_photon_umol_per_radiant_w": model.white.spectral_model.par_photon_umol_per_radiant_w,
                "nominal_radiant_efficiency": model.white.spectral_model.nominal_radiant_efficiency,
                "spectral_components": [
                    {
                        "label": comp.label,
                        "count": comp.count,
                        "nominal_package_w": comp.nominal_package_w,
                        "nominal_ppe_umol_per_j": comp.nominal_ppe_umol_per_j,
                        "spd_path": comp.spd_path,
                        "spectral_par_photon_umol_per_radiant_w": comp.spd_metrics.par_photon_umol_per_radiant_w,
                        "spectral_par_centroid_nm": comp.spd_metrics.par_centroid_nm,
                    }
                    for comp in model.white.spectral_model.components
                ],
            },
            "red": {
                "count": model.red.count,
                "nominal_package_w": model.red.nominal_package_w,
                "nominal_ppe_umol_per_j": model.red.nominal_ppe_umol_per_j,
                "nominal_current_a": model.red.nominal_current_a,
                "nominal_vf_v": model.red.nominal_vf_v,
                "nominal_rel_ppe_multiplier_raw": model.red.nominal_rel_ppe_multiplier,
                "spectral_par_photon_umol_per_radiant_w": model.red.spectral_model.par_photon_umol_per_radiant_w,
                "nominal_radiant_efficiency": model.red.spectral_model.nominal_radiant_efficiency,
                "spectral_components": [
                    {
                        "label": comp.label,
                        "count": comp.count,
                        "nominal_package_w": comp.nominal_package_w,
                        "nominal_ppe_umol_per_j": comp.nominal_ppe_umol_per_j,
                        "spd_path": comp.spd_path,
                        "spectral_par_photon_umol_per_radiant_w": comp.spd_metrics.par_photon_umol_per_radiant_w,
                        "spectral_par_centroid_nm": comp.spd_metrics.par_centroid_nm,
                    }
                    for comp in model.red.spectral_model.components
                ],
            },
        },
        "curve_files": {
            "white_vf_csv": {
                "path": str(white_vf_path),
                "sha256": _sha256_file(white_vf_path),
            },
            "white_ppe_csv": {
                "path": str(white_ppe_path),
                "sha256": _sha256_file(white_ppe_path),
            },
            "red_vf_csv": {
                "path": str(red_vf_path),
                "sha256": _sha256_file(red_vf_path),
            },
            "red_ppe_csv": {
                "path": str(red_ppe_path),
                "sha256": _sha256_file(red_ppe_path),
            },
            "warm_white_spd_csv": {
                "path": str(ww_spd_path),
                "sha256": _sha256_file(ww_spd_path),
            },
            "cool_white_spd_csv": {
                "path": str(cw_spd_path),
                "sha256": _sha256_file(cw_spd_path),
            },
            "deep_red_spd_csv": {
                "path": str(red_spd_path),
                "sha256": _sha256_file(red_spd_path),
            },
        },
    }


def curve_model_manifest_json() -> str:
    return json.dumps(curve_model_manifest(), sort_keys=True)


def validate_curve_model() -> CurveValidationPayload:
    model = load_smd_curve_model()
    nominal_state = model.evaluate_module(
        model.nominal_module_input_w, pmma_active=True
    )
    return {
        "curve_model_version": CURVE_MODEL_VERSION,
        "nominal_input_w": nominal_state.input_w,
        "nominal_led_supply_w": nominal_state.led_supply_w,
        "nominal_source_umol_s": nominal_state.source_photon_umol_s,
        "nominal_output_umol_s": nominal_state.output_photon_umol_s,
        "nominal_wall_plug_ppe": nominal_state.wall_plug_ppe_umol_per_j,
        "nominal_source_ppe": nominal_state.source_ppe_umol_per_j,
        "nominal_output_ppe": nominal_state.output_ppe_umol_per_j,
        "thermal_multiplier": nominal_state.thermal_multiplier,
        "white_nominal_current_ma": model.white.nominal_current_a * 1000.0,
        "red_nominal_current_ma": model.red.nominal_current_a * 1000.0,
        "white_spectral_par_umol_per_radiant_w": model.white.spectral_model.par_photon_umol_per_radiant_w,
        "red_spectral_par_umol_per_radiant_w": model.red.spectral_model.par_photon_umol_per_radiant_w,
    }
