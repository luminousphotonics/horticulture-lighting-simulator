from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from rad_rebuild.radiance.config import (
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
)
from rad_rebuild.radiance.paths import RADIANCE_DATA_ROOT
from rad_rebuild.radiance.domain import (
    canonicalize_competitor_layout as _domain_competitor_layout,
)
from rad_rebuild.radiance.domain import canonicalize_system_mode
from rad_rebuild.radiance.settings import load_settings
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    NOMINAL_FIXTURE_PPF_UMOL_S as DEFAULT_HPS_FIXTURE_PPF,
    NOMINAL_INPUT_WATTS as DEFAULT_HPS_INPUT_WATTS,
    PROFILE_VERSION as HPS_PROFILE_VERSION,
    normalize_ies_variant as normalize_hps_ies_variant,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import (
    DEFAULT_SMD_BASIS_BACKEND,
    BASIS_BACKEND_RTRACE,
    basis_backend_request_fields,
    canonicalize_basis_backend,
)
from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
    CURVE_MODEL_VERSION as SMD_CURVE_MODEL_VERSION,
)
from rad_rebuild.radiance.engine.emitters.smd_generation.module_profile import (
    MODULE_PROFILE_VERSION,
)
from rad_rebuild.radiance.engine.simulation.precomputed_integrity import (
    PrecomputedIntegrityError,
    load_json_object,
    resolve_artifact_map,
    verify_basis_hash,
)


SCHEMA_VERSION = 1
SMD_LAYOUT_FAMILY = "horticultural_tiled_v1"
SMD_MODULE_PROFILE = MODULE_PROFILE_VERSION
DEFAULT_SENSOR_GRID_PROFILE = "adaptive_centered_v1"
DEFAULT_SENSOR_GRID_SPACING_M = 0.25
DIALUX_SENSOR_GRID_PROFILE = "dialux_15x15_edge_v1"
COMPETITOR_FIXTURE_INPUT_W = 800.0
COMPETITOR_FIXTURE_PPE_UMOL_PER_J = 2.8
COMPETITOR_FIXTURE_PPF_UMOL_S = (
    COMPETITOR_FIXTURE_INPUT_W * COMPETITOR_FIXTURE_PPE_UMOL_PER_J
)
COMPETITOR_PACKING_PROFILE = "exact_footprint_margin1_v1"
COMPETITOR_PRACTICAL_PROFILE = "smd_gap_threshold_v1"
HPS_LAYOUT_PROFILE = "coverage_grid_margin1_v1"
PRECOMPUTED_MODE_ENV = "RADIANCE_PRECOMPUTED_MODE"
PRECOMPUTED_ROOT_ENV = "RADIANCE_PRECOMPUTED_ROOT"
DEFAULT_ROOT_NAME = "precomputed"
JsonObject = dict[str, Any]


def canonical_mode(mode: str) -> str:
    return canonicalize_system_mode(mode, default=None).value


def mode_slug(mode: str) -> str:
    mode = canonical_mode(mode)
    if mode == MODE_COMPETITOR:
        return "competitor"
    if mode == MODE_HPS:
        return "hps"
    return "smd"


def hps_coverage_slug(coverage_ft: float | int | str | None) -> str:
    if coverage_ft is None:
        return "hps"
    try:
        cov = float(coverage_ft)
    except Exception:
        return "hps"
    if cov <= 0:
        return "hps"
    rounded = int(round(cov))
    if abs(cov - rounded) <= 1e-6:
        return f"hps_{rounded}x{rounded}"
    clean = str(cov).replace(".", "_")
    return f"hps_{clean}ft"


def hps_variant_slug(variant: str | None) -> str:
    raw = normalize_hps_ies_variant(variant)
    clean = "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")
    return clean or DEFAULT_HPS_IES_VARIANT


def canonical_competitor_layout(layout: str | None) -> str:
    return _domain_competitor_layout(layout).value


def bundle_mode_dirname(mode: str, req: Any | None = None) -> str:
    canonical = canonical_mode(mode)
    if canonical == MODE_COMPETITOR:
        if req is None:
            return mode_slug(canonical)
        layout = canonical_competitor_layout(getattr(req, "competitor_layout", "full"))
        return "competitor_practical" if layout == "practical" else mode_slug(canonical)
    if canonical != MODE_HPS:
        if req is None:
            return mode_slug(canonical)
        backend = canonicalize_basis_backend(
            getattr(req, "basis_backend", DEFAULT_SMD_BASIS_BACKEND)
        )
        if backend == BASIS_BACKEND_RTRACE:
            return mode_slug(canonical)
        return f"{mode_slug(canonical)}_{backend}"
    if req is None:
        return mode_slug(canonical)
    coverage = getattr(req, "hps_coverage_ft", None)
    variant = hps_variant_slug(getattr(req, "hps_ies_variant", DEFAULT_HPS_IES_VARIANT))
    coverage_slug = hps_coverage_slug(coverage).removeprefix("hps_")
    return f"hps_{variant}_{coverage_slug}"


def canonical_dims_ft(length_ft: float, width_ft: float) -> tuple[int, int] | None:
    try:
        length = float(length_ft)
        width = float(width_ft)
    except Exception:
        return None
    if length <= 0 or width <= 0:
        return None
    long_side = max(length, width)
    short_side = min(length, width)
    long_int = int(round(long_side))
    short_int = int(round(short_side))
    if abs(long_side - long_int) > 1e-6 or abs(short_side - short_int) > 1e-6:
        return None
    return long_int, short_int


def bundle_slug(length_ft: int, width_ft: int) -> str:
    return f"{int(length_ft)}x{int(width_ft)}"


def default_precomputed_root(engine_root: Path | None = None) -> Path:
    return RADIANCE_DATA_ROOT / DEFAULT_ROOT_NAME


def resolve_precomputed_root(engine_root: Path | None = None) -> Path:
    return load_settings().paths.precomputed_root


def effective_precomputed_mode(engine_root: Path) -> str:
    configured = load_settings().precomputed_mode
    if configured is not None:
        return configured.value
    if resolve_precomputed_root(engine_root).exists():
        return "prefer"
    return "off"


@dataclass(frozen=True)
class BundleRef:
    engine_root: Path
    dataset_root: Path
    mode: str
    mode_dirname: str
    length_ft: int
    width_ft: int

    @property
    def slug(self) -> str:
        return bundle_slug(self.length_ft, self.width_ft)

    @property
    def mode_dir(self) -> Path:
        return self.dataset_root / self.mode_dirname

    @property
    def path(self) -> Path:
        return self.mode_dir / self.slug

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"


def bundle_ref(
    engine_root: Path,
    mode: str,
    length_ft: float,
    width_ft: float,
    dataset_root: Path | None = None,
    req: Any | None = None,
) -> BundleRef | None:
    dims = canonical_dims_ft(length_ft, width_ft)
    if dims is None:
        return None
    root = dataset_root or resolve_precomputed_root(engine_root)
    return BundleRef(
        engine_root=engine_root,
        dataset_root=root,
        mode=canonical_mode(mode),
        mode_dirname=bundle_mode_dirname(mode, req),
        length_ft=dims[0],
        width_ft=dims[1],
    )


def load_manifest(ref: BundleRef) -> dict[str, Any] | None:
    if not ref.manifest_path.exists():
        return None
    try:
        data = load_json_object(ref.manifest_path)
    except (OSError, json.JSONDecodeError, PrecomputedIntegrityError):
        return None
    return data


def required_artifacts(manifest: dict[str, Any]) -> list[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    out: list[str] = []
    for value in artifacts.values():
        if isinstance(value, str) and value:
            out.append(value)
    return out


def bundle_complete(ref: BundleRef) -> bool:
    manifest = load_manifest(ref)
    if not manifest:
        return False
    if int(manifest.get("schema_version", 0) or 0) != SCHEMA_VERSION:
        return False
    try:
        resolved = resolve_artifact_map(ref.path, manifest)
        verify_basis_hash(manifest, resolved)
    except (OSError, ValueError, PrecomputedIntegrityError):
        return False
    return True


def params_match(manifest: dict[str, Any], expected: dict[str, Any]) -> bool:
    actual = manifest.get("request_params")
    if not isinstance(actual, dict):
        return False
    for key, value in expected.items():
        if (
            key == "output_policy"
            and value == "fixed_output_v1"
            and "output_policy" not in actual
            and canonical_mode(str(manifest.get("mode", ""))) == MODE_HPS
        ):
            continue
        if (
            key == "basis_backend"
            and value == BASIS_BACKEND_RTRACE
            and "basis_backend" not in actual
            and canonical_mode(str(manifest.get("mode", ""))) == MODE_SMD
        ):
            continue
        if actual.get(key) != value:
            return False
    return True


def request_params_for_mode(
    req: Any, env: Mapping[str, Any] | None = None
) -> JsonObject:
    mode = canonical_mode(getattr(req, "mode", MODE_SMD))
    base: JsonObject = {
        "subpatch_grid": int(getattr(req, "subpatch_grid", 1)),
        "mount_z_m": round(float(getattr(req, "mount_z_m", 0.4572)), 6),
        "dialux_sensor_grid": bool(getattr(req, "dialux_sensor_grid", False)),
    }
    if base["dialux_sensor_grid"]:
        base["sensor_grid_profile"] = DIALUX_SENSOR_GRID_PROFILE
    else:
        base["sensor_grid_profile"] = DEFAULT_SENSOR_GRID_PROFILE
        base["sensor_grid_spacing_m"] = round(DEFAULT_SENSOR_GRID_SPACING_M, 6)
    if mode == MODE_COMPETITOR:
        layout = canonical_competitor_layout(getattr(req, "competitor_layout", "full"))
        base.update(
            {
                "sp_ppf": round(
                    float(getattr(req, "sp_ppf", COMPETITOR_FIXTURE_PPF_UMOL_S)), 6
                ),
                "sp_z_m": round(float(getattr(req, "sp_z_m", 0.4572)), 6),
                "sp_ppe": round(
                    float(getattr(req, "sp_ppe", COMPETITOR_FIXTURE_PPE_UMOL_PER_J)), 6
                ),
                "outer_margin_in": 1.0,
            }
        )
        if layout == "practical":
            base.update(
                {
                    "layout_policy": "practical",
                    "packing_profile": COMPETITOR_PRACTICAL_PROFILE,
                    "gap_source": "smd_exact_tiled_clear_gap_v1",
                }
            )
        else:
            base.update({"packing_profile": COMPETITOR_PACKING_PROFILE})
        return base
    if mode == MODE_HPS:
        base.update(
            {
                "hps_coverage_ft": round(
                    float(getattr(req, "hps_coverage_ft", 4.0)), 6
                ),
                "hps_z_m": round(
                    float(getattr(req, "hps_z_m", DEFAULT_HPS_MOUNT_Z_M)), 6
                ),
                "hps_fixture_ppf": round(
                    float(getattr(req, "hps_fixture_ppf", DEFAULT_HPS_FIXTURE_PPF)), 6
                ),
                "hps_input_watts": round(
                    float(getattr(req, "hps_input_watts", DEFAULT_HPS_INPUT_WATTS)), 6
                ),
                "hps_ies_variant": normalize_hps_ies_variant(
                    getattr(req, "hps_ies_variant", DEFAULT_HPS_IES_VARIANT)
                ),
                "outer_margin_in": 1.0,
                "layout_profile": HPS_LAYOUT_PROFILE,
                "fixture_profile": HPS_PROFILE_VERSION,
                "output_policy": "fixed_output_v1",
            }
        )
        return base
    base.update(
        {
            "smd_base_ring": int(getattr(req, "smd_base_ring", 0)),
            "layout_family": SMD_LAYOUT_FAMILY,
            "module_profile": SMD_MODULE_PROFILE,
            "smd_model": "legacy"
            if bool(getattr(req, "match_system_ppe", False))
            else "curve",
            "smd_curve_model": SMD_CURVE_MODEL_VERSION,
            "match_system_ppe": bool(getattr(req, "match_system_ppe", False)),
        }
    )
    base.update(
        basis_backend_request_fields(
            getattr(req, "basis_backend", DEFAULT_SMD_BASIS_BACKEND),
            sim_mode=getattr(req, "sim_mode", "standard"),
            env=env or {},
        )
    )
    return base
