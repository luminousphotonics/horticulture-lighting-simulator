from __future__ import annotations

import json
import math
import re
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
    RadianceRunRequest,
    canonicalize_competitor_layout as _domain_competitor_layout,
    canonicalize_system_mode,
    plant_geometry_config_from_request,
    request_with_updates,
)
from rad_rebuild.radiance.engine.plants.leaf_materials import (
    DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE,
    SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED,
    normalize_fspm_spectral_transport_mode,
    normalize_leaf_radiance_material_mode,
)
from rad_rebuild.radiance.engine.plants.layout import (
    canonical_room_dimensions_ft,
    fit_plant_grid,
)
from rad_rebuild.radiance.engine.plants.optical_profiles import (
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
)
from rad_rebuild.radiance.engine.plants.surface_flux import (
    RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
    RECEIVER_GRANULARITY_MESH_PATCH,
    normalize_receiver_granularity,
)
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
FEET_TO_METERS = 0.3048
PRECOMPUTED_PLANT_SPACING_M = 0.40
PRECOMPUTED_FSPM_RECEIVER_GRANULARITY = RECEIVER_GRANULARITY_MESH_PATCH
SUPPORTED_PRECOMPUTED_FSPM_RECEIVER_GRANULARITIES = (
    RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
    RECEIVER_GRANULARITY_MESH_PATCH,
)
PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE = normalize_fspm_spectral_transport_mode(
    SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED
)
PRECOMPUTED_PLANT_RECEIVER_SCHEMA = "rad_rebuild.precomputed.plant_receiver.v1"
PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION = 1
PRECOMPUTED_PLANT_RECEIVER_JSON_ARTIFACT_KEY = "plant_receiver_json"
PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_ARTIFACT_KEY = "plant_receiver_basis_A_npy"
PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_ARTIFACT_KEY = "plant_receiver_json_gz"
PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY = "plant_receiver_basis_A_npz"
PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY = "plant_receiver_npz"
PRECOMPUTED_PLANT_RECEIVER_JSON_FILENAME = "plant_receiver.json"
PRECOMPUTED_PLANT_RECEIVER_BASIS_NPY_FILENAME = "plant_receiver_basis_A.npy"
PRECOMPUTED_PLANT_RECEIVER_JSON_GZ_FILENAME = "plant_receiver.json.gz"
PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_FILENAME = "plant_receiver_basis_A.npz"
PRECOMPUTED_PLANT_RECEIVER_NPZ_FILENAME = "plant_receiver.npz"
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
_PLANT_GRID_SURFACE_ID_RE = re.compile(
    r"^plant_r(?P<row>\d+)_c(?P<column>\d+)_leaf_(?P<leaf>\d+)_face_(?P<face>\d+)$"
)


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


def precomputed_plant_density(
    length_ft: float | int,
    width_ft: float | int,
    *,
    spacing_m: float = PRECOMPUTED_PLANT_SPACING_M,
) -> tuple[int, int]:
    canonical_length_ft, canonical_width_ft = canonical_room_dimensions_ft(
        length_ft,
        width_ft,
    )
    layout = fit_plant_grid(
        canonical_length_ft,
        canonical_width_ft,
        target_spacing_m=spacing_m,
    )
    return (layout.length.count, layout.width.count)


def canonical_plant_enabled_precomputed_request(
    req: RadianceRunRequest,
    *,
    receiver_granularity: str | None = None,
) -> RadianceRunRequest:
    plant_rows, plant_columns = precomputed_plant_density(
        req.length_ft,
        req.width_ft,
    )
    canonical_length_ft, canonical_width_ft = canonical_room_dimensions_ft(
        req.length_ft,
        req.width_ft,
    )
    return request_with_updates(
        req,
        length_ft=canonical_length_ft,
        width_ft=canonical_width_ft,
        plants_enabled=True,
        plant_rows=plant_rows,
        plant_columns=plant_columns,
        plant_spacing_m=PRECOMPUTED_PLANT_SPACING_M,
        sim_mode="standard",
        match_system_ppe=(canonical_mode(req.mode) == MODE_SMD),
        fspm_receiver_granularity=normalize_receiver_granularity(
            receiver_granularity or PRECOMPUTED_FSPM_RECEIVER_GRANULARITY
        ),
        fspm_spectral_transport_mode=PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
    )


def _json_number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return float(default)
    number = float(value)
    return number if math.isfinite(number) else float(default)


def _plant_receiver_row_from_surface_summary(row: Mapping[str, Any]) -> JsonObject:
    surface_id = row.get("surface_id")
    if not isinstance(surface_id, str) or not surface_id:
        raise ValueError("Plant receiver surface row is missing surface_id.")
    stored_ppfd = _json_number(
        row.get("incident_photon_flux_density_umol_m2_s"),
        default=-1.0,
    )
    if stored_ppfd < 0.0:
        raise ValueError(
            f"Plant receiver surface row {surface_id!r} is missing incident PPFD."
        )
    out: JsonObject = {
        "surface_id": surface_id,
        "stored_ppfd_umol_m2_s": stored_ppfd,
    }
    for key in ("plant_id", "leaf_id", "leaf_index", "face_index"):
        if key in row:
            out[key] = row[key]
    if "area_m2" in row:
        out["area_m2"] = _json_number(row.get("area_m2"))
    for key in ("centroid_m", "normal"):
        value = row.get(key)
        if isinstance(value, list):
            out[key] = list(value)
    for key in (
        "receiver_sample_count",
        "receiver_rows_per_mesh_surface_row",
        "receiver_sides",
        "side_summaries",
        "visual_granularity",
    ):
        if key in row:
            out[key] = row[key]
    return out


def _receiver_density_from_mapping(row: Mapping[str, Any]) -> float | None:
    for key in (
        "incident_photon_flux_density_umol_m2_s",
        "stored_ppfd_umol_m2_s",
        "receiver_ppfd_umol_m2_s",
        "runtime_ppfd_umol_m2_s",
    ):
        value = row.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            number = float(value)
            if math.isfinite(number) and number >= 0.0:
                return number
    return None


def _plant_receiver_sample_from_side_summary(
    surface_row: Mapping[str, Any],
    side_row: Mapping[str, Any],
    *,
    index: int,
) -> JsonObject | None:
    surface_id = surface_row.get("surface_id")
    if not isinstance(surface_id, str) or not surface_id:
        return None
    stored_ppfd = _receiver_density_from_mapping(side_row)
    if stored_ppfd is None:
        return None
    side = str(side_row.get("side") or side_row.get("receiver_side") or f"sample_{index}")
    out: JsonObject = {
        "sample_id": str(side_row.get("sample_id") or f"{surface_id}_{side}"),
        "surface_id": surface_id,
        "side": side,
        "stored_ppfd_umol_m2_s": stored_ppfd,
    }
    for key in ("plant_id", "leaf_id", "leaf_index", "face_index", "area_m2"):
        if key in surface_row:
            out[key] = surface_row[key]
    for key in ("centroid_m", "normal"):
        value = side_row.get(key, surface_row.get(key))
        if isinstance(value, list):
            out[key] = list(value)
    return out


def _plant_receiver_samples_from_surface_rows(
    raw_rows: list[Mapping[str, Any]],
) -> list[JsonObject]:
    samples: list[JsonObject] = []
    for surface_row in raw_rows:
        side_summaries = surface_row.get("side_summaries")
        if not isinstance(side_summaries, list):
            continue
        for index, side_row in enumerate(side_summaries):
            if not isinstance(side_row, Mapping):
                continue
            sample = _plant_receiver_sample_from_side_summary(
                surface_row,
                side_row,
                index=index,
            )
            if sample is not None:
                samples.append(sample)
    return samples


def _plant_receiver_sample_from_detail_row(row: Mapping[str, Any]) -> JsonObject | None:
    surface_id = row.get("surface_id")
    if not isinstance(surface_id, str) or not surface_id:
        return None
    side = row.get("side") or row.get("receiver_side")
    if not isinstance(side, str) or not side:
        return None
    stored_ppfd = _receiver_density_from_mapping(row)
    if stored_ppfd is None:
        return None
    out: JsonObject = {
        "sample_id": str(row.get("sample_id") or f"{surface_id}_{side}"),
        "surface_id": surface_id,
        "side": side,
        "stored_ppfd_umol_m2_s": stored_ppfd,
    }
    for key in ("plant_id", "leaf_id", "leaf_index", "face_index", "area_m2"):
        if key in row:
            out[key] = row[key]
    for key in ("centroid_m", "normal"):
        value = row.get(key)
        if isinstance(value, list):
            out[key] = list(value)
    return out


def _collect_receiver_samples_from_detail(value: Any) -> list[JsonObject]:
    samples: list[JsonObject] = []
    if isinstance(value, Mapping):
        sample = _plant_receiver_sample_from_detail_row(value)
        if sample is not None:
            samples.append(sample)
        for child in value.values():
            samples.extend(_collect_receiver_samples_from_detail(child))
    elif isinstance(value, list):
        for child in value:
            samples.extend(_collect_receiver_samples_from_detail(child))
    return samples


def _plant_receiver_samples_from_surface_flux_payload(
    surface_flux_payload: Mapping[str, Any],
    raw_rows: list[Mapping[str, Any]],
) -> list[JsonObject]:
    if normalize_receiver_granularity(
        surface_flux_payload.get("receiver_granularity")
    ) != RECEIVER_GRANULARITY_MESH_PATCH:
        return []
    samples = _plant_receiver_samples_from_surface_rows(raw_rows)
    if samples:
        return samples
    for key in ("raw_leaf_surface_flux_detail", "raw_surface_flux_detail"):
        detail = surface_flux_payload.get(key)
        if detail is not None:
            samples.extend(_collect_receiver_samples_from_detail(detail))
    visualization = surface_flux_payload.get("visualization")
    if isinstance(visualization, Mapping):
        detail = visualization.get("raw_leaf_surface_flux_detail")
        if detail is not None:
            samples.extend(_collect_receiver_samples_from_detail(detail))
    seen: set[str] = set()
    unique: list[JsonObject] = []
    for sample in samples:
        key = str(sample.get("sample_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(sample)
    return unique


def _plant_receiver_samples_from_receiver_trace(
    receiver_samples: list[Mapping[str, Any]],
    receiver_densities: list[float],
    *,
    receiver_scale_multiplier: float,
) -> list[JsonObject]:
    if len(receiver_samples) != len(receiver_densities):
        raise ValueError(
            "Plant receiver sample count does not match receiver density count."
        )
    scale = float(receiver_scale_multiplier)
    if not math.isfinite(scale) or scale < 0.0:
        raise ValueError("receiver_scale_multiplier must be finite and non-negative.")
    out: list[JsonObject] = []
    for index, (sample, density) in enumerate(
        zip(receiver_samples, receiver_densities, strict=True)
    ):
        surface_id = sample.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id:
            raise ValueError(f"Plant receiver sample {index} is missing surface_id.")
        side = sample.get("side")
        if not isinstance(side, str) or not side:
            raise ValueError(f"Plant receiver sample {index} is missing side.")
        value = float(density)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(
                f"Plant receiver sample {index} has invalid receiver density."
            )
        row: JsonObject = {
            "sample_id": str(sample.get("sample_id") or f"{surface_id}_{side}"),
            "surface_id": surface_id,
            "side": side,
            "stored_ppfd_umol_m2_s": value * scale,
        }
        for key in ("plant_id", "leaf_id", "leaf_index", "face_index", "area_m2"):
            if key in sample:
                row[key] = sample[key]
        for source_key, target_key in (
            ("centroid_m", "centroid_m"),
            ("normal", "normal"),
            ("direction", "normal"),
        ):
            value_obj = sample.get(source_key)
            if isinstance(value_obj, list) and target_key not in row:
                row[target_key] = list(value_obj)
        out.append(row)
    return out


def build_precomputed_plant_receiver_payload(
    surface_flux_payload: Mapping[str, Any],
    *,
    value_semantics: str,
    basis_metadata: Mapping[str, Any] | None = None,
    receiver_samples: list[Mapping[str, Any]] | None = None,
    receiver_densities: list[float] | None = None,
    receiver_scale_multiplier: float = 1.0,
) -> JsonObject:
    raw_rows = surface_flux_payload.get("surface_summaries")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError(
            "Plant surface-flux payload must include surface_summaries for "
            "precomputed receiver emission."
        )
    surface_rows = [row for row in raw_rows if isinstance(row, Mapping)]
    receiver_rows = [
        _plant_receiver_row_from_surface_summary(row)
        for row in surface_rows
    ]
    if len(receiver_rows) != len(raw_rows):
        raise ValueError("Plant receiver surface_summaries must be objects.")
    if receiver_samples is not None or receiver_densities is not None:
        if receiver_samples is None or receiver_densities is None:
            raise ValueError(
                "receiver_samples and receiver_densities must be provided together."
            )
        receiver_sample_rows = _plant_receiver_samples_from_receiver_trace(
            receiver_samples,
            receiver_densities,
            receiver_scale_multiplier=receiver_scale_multiplier,
        )
    else:
        receiver_sample_rows = _plant_receiver_samples_from_surface_flux_payload(
            surface_flux_payload,
            surface_rows,
        )
    payload: JsonObject = {
        "schema": PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
        "schema_version": PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
        "value_semantics": str(value_semantics),
        "receiver_granularity": surface_flux_payload.get("receiver_granularity"),
        "receiver_generation_basis": surface_flux_payload.get(
            "receiver_generation_basis"
        ),
        "receiver_area_basis": surface_flux_payload.get("receiver_area_basis"),
        "receiver_side_policy": surface_flux_payload.get("receiver_side_policy"),
        "normal_generation_basis": surface_flux_payload.get("normal_generation_basis"),
        "receiver_granularity_role": surface_flux_payload.get(
            "receiver_granularity_role"
        ),
        "leaf_material_profile_id": surface_flux_payload.get(
            "leaf_material_profile_id"
        ),
        "leaf_radiance_material_mode": surface_flux_payload.get(
            "leaf_radiance_material_mode"
        ),
        "fspm_spectral_transport_mode": surface_flux_payload.get(
            "fspm_spectral_transport_mode"
        ),
        "plant_count": surface_flux_payload.get("plant_count"),
        "leaf_count": surface_flux_payload.get("leaf_count"),
        "surface_count": surface_flux_payload.get("surface_count"),
        "receiver_sample_count": len(receiver_sample_rows)
        if receiver_sample_rows
        else surface_flux_payload.get("receiver_sample_count"),
        "surface_receivers": receiver_rows,
    }
    if receiver_sample_rows:
        payload["receiver_samples"] = receiver_sample_rows
    if basis_metadata:
        payload["basis_metadata"] = dict(basis_metadata)
    return payload


def write_precomputed_plant_receiver_payload(
    path: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _metadata_without_receiver_rows(payload: Mapping[str, Any]) -> JsonObject:
    return {
        str(key): value
        for key, value in payload.items()
        if key not in {"surface_receivers", "receiver_samples"}
    }


def _plant_grid_indices_from_surface_id(
    surface_id: str,
) -> tuple[int, int, int, int] | None:
    match = _PLANT_GRID_SURFACE_ID_RE.fullmatch(surface_id)
    if match is None:
        return None
    return (
        int(match.group("row")),
        int(match.group("column")),
        int(match.group("leaf")),
        int(match.group("face")),
    )


def _plant_receiver_npz_grid_columns(
    rows: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    import numpy as np

    plant_rows: list[int] = []
    plant_columns: list[int] = []
    leaf_indices: list[int] = []
    face_indices: list[int] = []
    stored_values: list[float] = []
    for row in rows:
        parsed = _plant_grid_indices_from_surface_id(str(row["surface_id"]))
        if parsed is None:
            return None
        plant_row, plant_column, leaf_index, face_index = parsed
        plant_rows.append(plant_row)
        plant_columns.append(plant_column)
        leaf_indices.append(leaf_index)
        face_indices.append(face_index)
        stored_values.append(_json_number(row.get("stored_ppfd_umol_m2_s")))
    return {
        "plant_row": np.asarray(plant_rows, dtype=np.uint16),
        "plant_column": np.asarray(plant_columns, dtype=np.uint16),
        "leaf_index": np.asarray(leaf_indices, dtype=np.uint16),
        "face_index": np.asarray(face_indices, dtype=np.uint16),
        "stored_ppfd_umol_m2_s": np.asarray(stored_values, dtype=np.float64),
    }


def _plant_receiver_npz_string_columns(
    rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    import numpy as np

    return {
        "surface_id": np.asarray([str(row["surface_id"]) for row in rows]),
        "plant_id": np.asarray([str(row.get("plant_id", "")) for row in rows]),
        "leaf_id": np.asarray([str(row.get("leaf_id", "")) for row in rows]),
        "leaf_index": np.asarray(
            [int(row.get("leaf_index", -1) or -1) for row in rows],
            dtype=np.int32,
        ),
        "face_index": np.asarray(
            [int(row.get("face_index", -1) or -1) for row in rows],
            dtype=np.int32,
        ),
        "stored_ppfd_umol_m2_s": np.asarray(
            [_json_number(row.get("stored_ppfd_umol_m2_s")) for row in rows],
            dtype=np.float64,
        ),
    }


def _plant_receiver_sample_npz_columns(
    samples: list[Mapping[str, Any]],
) -> dict[str, Any]:
    import numpy as np

    return {
        "sample_id": np.asarray([str(row.get("sample_id", "")) for row in samples]),
        "sample_surface_id": np.asarray([str(row["surface_id"]) for row in samples]),
        "sample_side": np.asarray([str(row.get("side", "")) for row in samples]),
        "sample_plant_id": np.asarray([str(row.get("plant_id", "")) for row in samples]),
        "sample_leaf_id": np.asarray([str(row.get("leaf_id", "")) for row in samples]),
        "sample_leaf_index": np.asarray(
            [int(row.get("leaf_index", -1) or -1) for row in samples],
            dtype=np.int32,
        ),
        "sample_face_index": np.asarray(
            [int(row.get("face_index", -1) or -1) for row in samples],
            dtype=np.int32,
        ),
        "sample_stored_ppfd_umol_m2_s": np.asarray(
            [_json_number(row.get("stored_ppfd_umol_m2_s")) for row in samples],
            dtype=np.float64,
        ),
    }


def write_precomputed_plant_receiver_npz(
    path: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    import numpy as np

    rows = _plant_receiver_payload_rows(payload)
    metadata = _metadata_without_receiver_rows(payload)
    columns = _plant_receiver_npz_grid_columns(rows)
    if columns is None:
        metadata["surface_receiver_encoding"] = "surface_id_strings_v1"
        columns = _plant_receiver_npz_string_columns(rows)
    else:
        metadata["surface_receiver_encoding"] = "plant_grid_indices_v1"
    samples = _plant_receiver_payload_samples(payload)
    if samples:
        metadata["receiver_sample_encoding"] = "mesh_patch_side_samples_v1"
        metadata["receiver_sample_count"] = len(samples)
        columns.update(_plant_receiver_sample_npz_columns(samples))
    columns["metadata_json"] = np.frombuffer(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        dtype=np.uint8,
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **columns)
    return out


def _npz_metadata_json(archive: Any, path: Path) -> JsonObject:
    if "metadata_json" not in archive:
        raise ValueError(f"Plant receiver NPZ {path} is missing metadata_json.")
    raw = archive["metadata_json"]
    metadata = json.loads(bytes(raw.tolist()).decode("utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"Plant receiver NPZ {path} metadata_json must be an object.")
    return metadata


def _npz_array_length(archive: Any, key: str, path: Path) -> int:
    if key not in archive:
        raise ValueError(f"Plant receiver NPZ {path} is missing {key}.")
    return int(len(archive[key]))


def _plant_receiver_rows_from_grid_npz(archive: Any, path: Path) -> list[JsonObject]:
    import numpy as np

    count = _npz_array_length(archive, "stored_ppfd_umol_m2_s", path)
    for key in ("plant_row", "plant_column", "leaf_index", "face_index"):
        if _npz_array_length(archive, key, path) != count:
            raise ValueError(f"Plant receiver NPZ {path} column {key} length mismatch.")
    stored = np.asarray(archive["stored_ppfd_umol_m2_s"], dtype=np.float64)
    rows: list[JsonObject] = []
    for index in range(count):
        plant_row = int(archive["plant_row"][index])
        plant_column = int(archive["plant_column"][index])
        leaf_index = int(archive["leaf_index"][index])
        face_index = int(archive["face_index"][index])
        plant_id = f"plant_r{plant_row:03d}_c{plant_column:03d}"
        leaf_id = f"{plant_id}_leaf_{leaf_index:03d}"
        rows.append(
            {
                "surface_id": f"{leaf_id}_face_{face_index:04d}",
                "plant_id": plant_id,
                "leaf_id": leaf_id,
                "leaf_index": leaf_index,
                "face_index": face_index,
                "stored_ppfd_umol_m2_s": float(stored[index]),
            }
        )
    return rows


def _plant_receiver_rows_from_string_npz(archive: Any, path: Path) -> list[JsonObject]:
    import numpy as np

    count = _npz_array_length(archive, "stored_ppfd_umol_m2_s", path)
    surface_ids = archive["surface_id"] if "surface_id" in archive else None
    if surface_ids is None or len(surface_ids) != count:
        raise ValueError(f"Plant receiver NPZ {path} surface_id length mismatch.")
    stored = np.asarray(archive["stored_ppfd_umol_m2_s"], dtype=np.float64)
    rows: list[JsonObject] = []
    for index in range(count):
        row: JsonObject = {
            "surface_id": str(surface_ids[index]),
            "stored_ppfd_umol_m2_s": float(stored[index]),
        }
        for key in ("plant_id", "leaf_id"):
            if key in archive:
                value = str(archive[key][index])
                if value:
                    row[key] = value
        for key in ("leaf_index", "face_index"):
            if key in archive:
                row[key] = int(archive[key][index])
        rows.append(row)
    return rows


def _plant_receiver_samples_from_npz(archive: Any, metadata: Mapping[str, Any], path: Path) -> list[JsonObject]:
    import numpy as np

    encoding = str(metadata.get("receiver_sample_encoding") or "")
    if not encoding:
        return []
    if encoding != "mesh_patch_side_samples_v1":
        raise ValueError(
            f"Unsupported plant receiver NPZ receiver_sample_encoding {encoding!r} in {path}."
        )
    count = _npz_array_length(archive, "sample_stored_ppfd_umol_m2_s", path)
    for key in ("sample_surface_id", "sample_side"):
        if _npz_array_length(archive, key, path) != count:
            raise ValueError(f"Plant receiver NPZ {path} column {key} length mismatch.")
    stored = np.asarray(archive["sample_stored_ppfd_umol_m2_s"], dtype=np.float64)
    samples: list[JsonObject] = []
    for index in range(count):
        surface_id = str(archive["sample_surface_id"][index])
        side = str(archive["sample_side"][index])
        row: JsonObject = {
            "sample_id": str(archive["sample_id"][index])
            if "sample_id" in archive
            else f"{surface_id}_{side}",
            "surface_id": surface_id,
            "side": side,
            "stored_ppfd_umol_m2_s": float(stored[index]),
        }
        for archive_key, row_key in (
            ("sample_plant_id", "plant_id"),
            ("sample_leaf_id", "leaf_id"),
        ):
            if archive_key in archive:
                value = str(archive[archive_key][index])
                if value:
                    row[row_key] = value
        for archive_key, row_key in (
            ("sample_leaf_index", "leaf_index"),
            ("sample_face_index", "face_index"),
        ):
            if archive_key in archive:
                row[row_key] = int(archive[archive_key][index])
        samples.append(row)
    return samples


def load_precomputed_plant_receiver_npz(path: str | Path) -> JsonObject:
    import numpy as np

    npz_path = Path(path)
    with np.load(npz_path, allow_pickle=False) as archive:
        metadata = _npz_metadata_json(archive, npz_path)
        encoding = str(metadata.get("surface_receiver_encoding") or "")
        if encoding == "plant_grid_indices_v1":
            rows = _plant_receiver_rows_from_grid_npz(archive, npz_path)
        elif encoding == "surface_id_strings_v1":
            rows = _plant_receiver_rows_from_string_npz(archive, npz_path)
        else:
            raise ValueError(
                f"Unsupported plant receiver NPZ surface_receiver_encoding "
                f"{encoding!r} in {npz_path}."
            )
        samples = _plant_receiver_samples_from_npz(archive, metadata, npz_path)
    metadata.pop("surface_receiver_encoding", None)
    metadata.pop("receiver_sample_encoding", None)
    metadata["surface_receivers"] = rows
    if samples:
        metadata["receiver_samples"] = samples
    return metadata


def load_precomputed_plant_receiver_npz_compact(path: str | Path) -> JsonObject:
    import numpy as np

    npz_path = Path(path)
    with np.load(npz_path, allow_pickle=False) as archive:
        metadata = _npz_metadata_json(archive, npz_path)
        encoding = str(metadata.get("surface_receiver_encoding") or "")
        if encoding == "plant_grid_indices_v1":
            surface_count = _npz_array_length(archive, "stored_ppfd_umol_m2_s", npz_path)
            for key in ("plant_row", "plant_column", "leaf_index", "face_index"):
                if _npz_array_length(archive, key, npz_path) != surface_count:
                    raise ValueError(
                        f"Plant receiver NPZ {npz_path} column {key} length mismatch."
                    )
            metadata["surface_receiver_encoding"] = encoding
            metadata["surface_count"] = surface_count
            metadata["_surface_arrays"] = {
                "plant_row": np.asarray(archive["plant_row"], dtype=np.uint16),
                "plant_column": np.asarray(archive["plant_column"], dtype=np.uint16),
                "leaf_index": np.asarray(archive["leaf_index"], dtype=np.uint16),
                "face_index": np.asarray(archive["face_index"], dtype=np.uint16),
                "stored_ppfd_umol_m2_s": np.asarray(
                    archive["stored_ppfd_umol_m2_s"], dtype=np.float64
                ),
            }
        else:
            rows = (
                _plant_receiver_rows_from_string_npz(archive, npz_path)
                if encoding == "surface_id_strings_v1"
                else []
            )
            metadata["surface_receivers"] = rows
            metadata["surface_count"] = len(rows)
        sample_encoding = str(metadata.get("receiver_sample_encoding") or "")
        if sample_encoding:
            if sample_encoding != "mesh_patch_side_samples_v1":
                raise ValueError(
                    f"Unsupported plant receiver NPZ receiver_sample_encoding "
                    f"{sample_encoding!r} in {npz_path}."
                )
            sample_count = _npz_array_length(
                archive, "sample_stored_ppfd_umol_m2_s", npz_path
            )
            for key in ("sample_leaf_index", "sample_face_index", "sample_side"):
                if _npz_array_length(archive, key, npz_path) != sample_count:
                    raise ValueError(
                        f"Plant receiver NPZ {npz_path} column {key} length mismatch."
                    )
            for key in ("sample_leaf_id", "sample_plant_id"):
                if (
                    key in archive
                    and _npz_array_length(archive, key, npz_path) != sample_count
                ):
                    raise ValueError(
                        f"Plant receiver NPZ {npz_path} column {key} length mismatch."
                    )
            metadata["receiver_sample_encoding"] = sample_encoding
            metadata["receiver_sample_count"] = sample_count
            metadata["_sample_arrays"] = {
                "leaf_index": np.asarray(archive["sample_leaf_index"], dtype=np.int32),
                "face_index": np.asarray(archive["sample_face_index"], dtype=np.int32),
                "side": np.asarray(archive["sample_side"]),
                "stored_ppfd_umol_m2_s": np.asarray(
                    archive["sample_stored_ppfd_umol_m2_s"], dtype=np.float64
                ),
            }
            if "sample_leaf_id" in archive:
                metadata["_sample_arrays"]["leaf_id"] = np.asarray(
                    archive["sample_leaf_id"]
                )
            if "sample_plant_id" in archive:
                metadata["_sample_arrays"]["plant_id"] = np.asarray(
                    archive["sample_plant_id"]
                )
    return metadata


def _plant_receiver_payload_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = payload.get("surface_receivers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant receiver payload must include surface_receivers.")
    out: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"Plant receiver row {index} must be an object.")
        surface_id = row.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id:
            raise ValueError(f"Plant receiver row {index} is missing surface_id.")
        out.append(row)
    return out


def _plant_receiver_payload_samples(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = payload.get("receiver_samples")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("Plant receiver payload receiver_samples must be a list.")
    out: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"Plant receiver sample {index} must be an object.")
        surface_id = row.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id:
            raise ValueError(f"Plant receiver sample {index} is missing surface_id.")
        side = row.get("side")
        if not isinstance(side, str) or not side:
            raise ValueError(f"Plant receiver sample {index} is missing side.")
        out.append(row)
    return out


def validate_mesh_patch_plant_receiver_payload(
    payload: Mapping[str, Any],
    *,
    basis_row_count: int | None = None,
) -> list[str]:
    reasons: list[str] = []
    if normalize_receiver_granularity(payload.get("receiver_granularity")) != (
        RECEIVER_GRANULARITY_MESH_PATCH
    ):
        return reasons
    try:
        surfaces = _plant_receiver_payload_rows(payload)
    except ValueError as exc:
        return [str(exc)]
    try:
        samples = _plant_receiver_payload_samples(payload)
    except ValueError as exc:
        return [str(exc)]
    if not samples:
        reasons.append("mesh_patch plant receiver payload is missing receiver_samples.")
        return reasons
    expected_samples = len(surfaces) * 2
    if len(samples) != expected_samples:
        reasons.append(
            "mesh_patch receiver sample count must be 2 * surface row count "
            f"({expected_samples}); got {len(samples)}."
        )
    sides = {str(row.get("side") or "") for row in samples}
    if not {"front", "back"}.issubset(sides):
        reasons.append("mesh_patch receiver_samples must include front and back sides.")
    if basis_row_count is not None and int(basis_row_count) != len(samples):
        reasons.append(
            "mesh_patch plant receiver basis rows must match receiver sample count "
            f"({len(samples)}); got {basis_row_count}."
        )
    return reasons


def inspect_precomputed_plant_receiver_bundle(
    bundle_dir: str | Path,
    manifest: Mapping[str, Any] | None = None,
) -> JsonObject:
    import numpy as np

    def _array_length(archive: Any, key: str) -> int:
        if key not in archive.files:
            return 0
        shape = tuple(getattr(archive[key], "shape", ()))
        return int(shape[0]) if shape else 0

    def _basis_row_count(path: Path) -> int | None:
        with np.load(path, allow_pickle=False) as archive:
            if not archive.files:
                return None
            key = (
                "plant_receiver_basis_A"
                if "plant_receiver_basis_A" in archive.files
                else archive.files[0]
            )
            shape = tuple(getattr(archive[key], "shape", ()))
            return int(shape[0]) if shape else 0

    root = Path(bundle_dir)
    data = dict(manifest) if manifest is not None else load_json_object(root / "manifest.json")
    resolved = resolve_artifact_map(root, data)
    receiver_path = resolved.get(PRECOMPUTED_PLANT_RECEIVER_NPZ_ARTIFACT_KEY)
    basis_path = resolved.get(PRECOMPUTED_PLANT_RECEIVER_BASIS_NPZ_ARTIFACT_KEY)
    surface_rows = 0
    receiver_samples = 0
    side_sample_arrays_exist = False
    receiver_sides: list[str] = []
    if receiver_path is not None:
        with np.load(receiver_path, allow_pickle=False) as archive:
            metadata = _npz_metadata_json(archive, Path(receiver_path))
            surface_rows = _array_length(archive, "stored_ppfd_umol_m2_s")
            side_sample_arrays_exist = all(
                key in archive.files
                for key in (
                    "sample_surface_id",
                    "sample_side",
                    "sample_leaf_index",
                    "sample_face_index",
                    "sample_stored_ppfd_umol_m2_s",
                )
            )
            if "sample_stored_ppfd_umol_m2_s" in archive.files:
                receiver_samples = _array_length(
                    archive,
                    "sample_stored_ppfd_umol_m2_s",
                )
            else:
                receiver_samples = int(metadata.get("receiver_sample_count") or 0)
            if "sample_side" in archive.files:
                receiver_sides = sorted(
                    {
                        str(value)
                        for value in np.unique(archive["sample_side"])
                        if str(value)
                    }
                )
    basis_rows = None
    if basis_path is not None:
        basis_rows = _basis_row_count(Path(basis_path))
    request_params = (
        data.get("request_params")
        if isinstance(data.get("request_params"), Mapping)
        else {}
    )
    reasons: list[str] = []
    if (
        isinstance(request_params, Mapping)
        and request_params.get("plants_enabled") is True
        and request_params.get("fspm_receiver_granularity") == RECEIVER_GRANULARITY_MESH_PATCH
    ):
        if receiver_path is None:
            reasons.append("bundle is missing plant_receiver_npz.")
        if basis_path is None and canonical_mode(str(data.get("mode", ""))) == MODE_SMD:
            reasons.append("SMD mesh_patch bundle is missing plant_receiver_basis_A_npz.")
        if not side_sample_arrays_exist:
            reasons.append("plant_receiver.npz is missing side-specific sample arrays.")
        if receiver_samples != surface_rows * 2:
            reasons.append(
                "receiver sample count must be 2 * mesh surface row count "
                f"({surface_rows * 2}); got {receiver_samples}."
            )
        if basis_rows is not None and basis_rows != receiver_samples:
            reasons.append(
                "plant_receiver_basis_A row count must match receiver sample count "
                f"({receiver_samples}); got {basis_rows}."
            )
        if not {"front", "back"}.issubset(set(receiver_sides)):
            reasons.append("receiver side metadata must include front and back.")
    return {
        "surface_row_count": surface_rows,
        "receiver_sample_count": receiver_samples,
        "basis_row_count": basis_rows,
        "side_sample_arrays_exist": side_sample_arrays_exist,
        "receiver_sides": receiver_sides,
        "ok": not reasons,
        "reasons": sorted(set(reasons)),
    }


def _plant_receiver_basis_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    samples = _plant_receiver_payload_samples(payload)
    return samples if samples else _plant_receiver_payload_rows(payload)


def build_smd_precomputed_plant_receiver_basis_payload(
    column_payloads: list[Mapping[str, Any]],
    *,
    basis_metadata: Mapping[str, Any] | None = None,
) -> tuple[JsonObject, Any]:
    if not column_payloads:
        raise ValueError("At least one plant receiver basis column is required.")
    first_rows = _plant_receiver_basis_rows(column_payloads[0])
    surface_order = [
        str(row.get("sample_id") or f"{row['surface_id']}:{row.get('side', '')}")
        for row in first_rows
    ]
    columns: list[list[float]] = []
    for column_index, payload in enumerate(column_payloads):
        rows = _plant_receiver_basis_rows(payload)
        column_order = [
            str(row.get("sample_id") or f"{row['surface_id']}:{row.get('side', '')}")
            for row in rows
        ]
        if column_order != surface_order:
            raise ValueError(
                "Plant receiver basis column surface order mismatch at "
                f"column {column_index}."
            )
        columns.append(
            [
                _json_number(row.get("stored_ppfd_umol_m2_s"), default=-1.0)
                for row in rows
            ]
        )
        if any(value < 0.0 for value in columns[-1]):
            raise ValueError(
                f"Plant receiver basis column {column_index} has invalid PPFD values."
            )

    import numpy as np

    matrix = np.asarray(columns, dtype=np.float64).T
    receiver_rows: list[JsonObject] = []
    receiver_samples = _plant_receiver_payload_samples(column_payloads[0])
    for row in first_rows:
        receiver = dict(row)
        receiver["stored_ppfd_umol_m2_s"] = 0.0
        receiver_rows.append(receiver)
    payload = dict(column_payloads[0])
    if receiver_samples:
        payload["receiver_samples"] = receiver_rows
        payload["surface_receivers"] = _plant_receiver_payload_rows(column_payloads[0])
    else:
        payload["surface_receivers"] = receiver_rows
    payload.update(
        {
            "schema": PRECOMPUTED_PLANT_RECEIVER_SCHEMA,
            "schema_version": PRECOMPUTED_PLANT_RECEIVER_SCHEMA_VERSION,
            "value_semantics": "smd_receiver_basis",
            "basis_metadata": {
                "plant_receiver_basis_shape": [
                    int(matrix.shape[0]),
                    int(matrix.shape[1]),
                ],
                **dict(basis_metadata or {}),
            },
        }
    )
    return payload, matrix


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


def _round_optional_float(value: Any) -> float:
    return round(float(value), 6)


def _plant_request_params(req: Any) -> JsonObject:
    if not bool(getattr(req, "plants_enabled", False)):
        return {}
    geometry = plant_geometry_config_from_request(req)
    return {
        "plants_enabled": True,
        "plant_seed": int(geometry.seed),
        "plant_rows": int(geometry.plant_grid_rows),
        "plant_columns": int(geometry.plant_grid_columns),
        "plant_spacing_m": _round_optional_float(geometry.plant_spacing_m),
        "plant_height_m": _round_optional_float(geometry.plant_height_m),
        "plant_canopy_radius_m": _round_optional_float(geometry.canopy_radius_m),
        "plant_leaf_count": int(geometry.leaf_count_per_plant),
        "plant_growth_stage": _round_optional_float(geometry.growth_stage),
        "fspm_receiver_granularity": normalize_receiver_granularity(
            getattr(
                req,
                "fspm_receiver_granularity",
                PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
            )
        ),
        "fspm_spectral_transport_mode": normalize_fspm_spectral_transport_mode(
            getattr(
                req,
                "fspm_spectral_transport_mode",
                PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
            )
        ),
        "fspm_leaf_optical_profile_id": str(
            getattr(
                req,
                "fspm_leaf_optical_profile_id",
                REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
            )
            or REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
        ),
        "fspm_leaf_radiance_material_mode": normalize_leaf_radiance_material_mode(
            getattr(
                req,
                "fspm_leaf_radiance_material_mode",
                DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE,
            )
        ),
    }


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
    base.update(_plant_request_params(req))
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
