from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import shutil
import time
import base64
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from fastapi import HTTPException

from rad_rebuild.radiance.domain import plant_geometry_config_from_request
from rad_rebuild.radiance.fspm_targets import (
    resolve_fspm_target_ppfd,
    resolve_fspm_target_tolerance,
)
from rad_rebuild.radiance.config import (
    COMPETITOR_FIXTURE_PPE_UMOL_PER_J,
    COMPETITOR_FIXTURE_PPF_UMOL_S,
    COMPETITOR_LAYOUT_FULL,
    COMPETITOR_LAYOUT_PRACTICAL,
    DEFAULT_EXECUTION_MODE,
    EXECUTION_MODE_PRECOMPUTED,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
    canonicalize_execution_mode,
    canonicalize_quality_preset,
    canonicalize_radiance_mode,
    overlay_for_mode,
)
from rad_rebuild.radiance.paths import (
    RADIANCE_ENGINE_PACKAGE_ROOT,
    RADIANCE_OUTPUT_ROOT,
    RADIANCE_SCRIPTS_ROOT,
)
from rad_rebuild.radiance.settings import get_settings

try:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
        DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
        DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
        NOMINAL_FIXTURE_PPF_UMOL_S as DEFAULT_HPS_FIXTURE_PPF,
        NOMINAL_INPUT_WATTS as DEFAULT_HPS_INPUT_WATTS,
        normalize_ies_variant as normalize_hps_ies_variant,
    )
    from rad_rebuild.radiance.engine.simulation.basis_backends import (
        DEFAULT_SMD_BASIS_BACKEND,
        canonicalize_basis_backend,
    )
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import canonical_competitor_layout
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import backend workspace dependencies: {e}") from e


_SETTINGS = get_settings()
SESSION_ARTIFACT_RETENTION_S = _SETTINGS.session_artifact_retention_s
HPS_MODE_LABEL = MODE_HPS


def _find_root() -> Path:
    root = RADIANCE_OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


ROOT = _find_root()
LAST_RUN_PATH = ROOT / "runtime_state" / "last_run.json"
SCRIPT_ROOT = RADIANCE_SCRIPTS_ROOT
ENGINE_PACKAGE_ROOT = RADIANCE_ENGINE_PACKAGE_ROOT
BASIS_ROOT = _SETTINGS.paths.basis_output_root
CACHE_ROOT = _SETTINGS.paths.cache_root
VISUALIZATION_ROOT = _SETTINGS.paths.visualization_output_root
for _runtime_dir in (ROOT / "runtime_state", BASIS_ROOT, CACHE_ROOT, VISUALIZATION_ROOT):
    _runtime_dir.mkdir(parents=True, exist_ok=True)

ARTIFACTS = ROOT / "artifacts"
CONTENT_ARTIFACTS = ARTIFACTS / "content"
STAGING_ARTIFACTS = ARTIFACTS / "staging"
SESSION_ARTIFACTS = ARTIFACTS / "session_grants"
for _artifact_dir in (ARTIFACTS, CONTENT_ARTIFACTS, STAGING_ARTIFACTS, SESSION_ARTIFACTS):
    _artifact_dir.mkdir(parents=True, exist_ok=True)
LAST_RADIANCE_REQ: dict[str, dict[str, Any]] = {}
REQUEST_FINGERPRINT_SCHEMA_VERSION = 1
LEGACY_WORKSPACE_KEY_POLICY = "explicit-read-only-migration-only"
WORKSPACE_RECORD_SCHEMA_VERSION = 1
ARTIFACT_TOKEN_SCHEMA_VERSION = 1
ARTIFACT_SECRET_PATH = ROOT / "runtime_state" / "artifact_token_secret.key"
WORKSPACE_STATE_FILENAME = "state.json"
WORKSPACE_INTEGRITY_FILENAME = "integrity_manifest.json"
WORKSPACE_LAST_RUN_FILENAME = "last_run.json"
WORKSPACE_STAGING_RETENTION_S = 3600
WORKSPACE_CLEANUP_MAX_RECORDS = 256
REQUEST_FINGERPRINT_FIELDS = (
    "mode",
    "execution_mode",
    "length_ft",
    "width_ft",
    "target_ppfd",
    "peak_capping_enabled",
    "run_basis",
    "w_min",
    "w_max",
    "sim_mode",
    "subpatch_grid",
    "mount_z_m",
    "overlay",
    "smd_base_ring",
    "basis_backend",
    "match_system_ppe",
    "sp_ppf",
    "sp_z_m",
    "sp_ppe",
    "competitor_layout",
    "hps_coverage_ft",
    "hps_z_m",
    "hps_fixture_ppf",
    "hps_input_watts",
    "hps_ies_variant",
    "dialux_sensor_grid",
)
PLANT_REQUEST_FINGERPRINT_FIELDS = (
    "plants_enabled",
    "plant_seed",
    "plant_rows",
    "plant_columns",
    "plant_spacing_m",
    "plant_height_m",
    "plant_canopy_radius_m",
    "plant_leaf_count",
    "plant_growth_stage",
    "fspm_receiver_granularity",
    "fspm_leaf_optical_profile_id",
    "fspm_leaf_radiance_material_mode",
    "fspm_spectral_transport_mode",
    "fspm_target_ppfd_umol_m2_s",
    "fspm_target_tolerance_umol_m2_s",
)


class WorkspaceState(str, Enum):
    ALLOCATED = "allocated"
    RUNNING = "running"
    STAGED = "staged"
    VERIFIED = "verified"
    COMMITTED = "committed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass(frozen=True)
class WorkspaceLease:
    session_id: str
    workspace_key: str
    fingerprint: str
    request_payload: dict[str, object]
    record_root: Path
    staging_root: Path
    staging_workspace: Path
    committed_workspace: Path
    artifact_token: str


def _aligned_dims_ft(length_ft: float, width_ft: float) -> tuple[float, float]:
    if width_ft > length_ft:
        return width_ft, length_ft
    return length_ft, width_ft


def _normalize_mode(mode: str) -> str:
    try:
        return canonicalize_radiance_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sanitize_session_id(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return "anon"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value)[:80].strip("._-")
    return safe or "anon"


def _sanitize_workspace_key(raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("._-")
    return safe or "default"


def _session_id_from_request(request: Any | None) -> str:
    if request is None:
        return "anon"
    query_value = request.query_params.get("session_id")
    header_value = request.headers.get("x-radiance-session")
    return _sanitize_session_id(query_value or header_value)


def _session_root(session_id: str) -> Path:
    root = SESSION_ARTIFACTS / _sanitize_session_id(session_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prune_old_sessions(now: float) -> None:
    if not SESSION_ARTIFACTS.exists():
        return
    live_sessions: set[str] = set()
    for session_dir in SESSION_ARTIFACTS.iterdir():
        if not session_dir.is_dir():
            continue
        session_id = session_dir.name
        try:
            age_s = now - session_dir.stat().st_mtime
        except Exception:
            age_s = 0.0
        if age_s > SESSION_ARTIFACT_RETENTION_S:
            try:
                shutil.rmtree(session_dir)
            except Exception:
                live_sessions.add(session_id)
                continue
            LAST_RADIANCE_REQ.pop(session_id, None)
        else:
            live_sessions.add(session_id)

    for session_id in list(LAST_RADIANCE_REQ.keys()):
        if session_id not in live_sessions:
            LAST_RADIANCE_REQ.pop(session_id, None)


def _workspace_key(
    mode: str,
    length_ft: float,
    width_ft: float,
    target_ppfd: float,
    peak_capping_enabled: bool,
    hps_coverage_ft: float,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    sim_mode: str = "standard",
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
    run_basis: bool = True,
    w_min: float = 0.0,
    w_max: float = 100.0,
    subpatch_grid: int = 1,
    overlay: str = "auto",
    smd_base_ring: int = 0,
    match_system_ppe: bool = False,
    sp_ppf: float = COMPETITOR_FIXTURE_PPF_UMOL_S,
    sp_ppe: float = COMPETITOR_FIXTURE_PPE_UMOL_PER_J,
    hps_fixture_ppf: float = DEFAULT_HPS_FIXTURE_PPF,
    hps_input_watts: float = DEFAULT_HPS_INPUT_WATTS,
    dialux_sensor_grid: bool = False,
) -> str:
    payload = canonical_request_fingerprint_payload(
        {
            "mode": mode,
            "execution_mode": execution_mode,
            "length_ft": length_ft,
            "width_ft": width_ft,
            "target_ppfd": target_ppfd,
            "peak_capping_enabled": peak_capping_enabled,
            "run_basis": run_basis,
            "w_min": w_min,
            "w_max": w_max,
            "sim_mode": sim_mode,
            "subpatch_grid": subpatch_grid,
            "mount_z_m": mount_z_m,
            "overlay": overlay,
            "smd_base_ring": smd_base_ring,
            "basis_backend": basis_backend,
            "match_system_ppe": match_system_ppe,
            "sp_ppf": sp_ppf,
            "sp_z_m": sp_z_m,
            "sp_ppe": sp_ppe,
            "competitor_layout": competitor_layout,
            "hps_coverage_ft": hps_coverage_ft,
            "hps_z_m": hps_z_m,
            "hps_fixture_ppf": hps_fixture_ppf,
            "hps_input_watts": hps_input_watts,
            "hps_ies_variant": hps_ies_variant,
            "dialux_sensor_grid": dialux_sensor_grid,
        }
    )
    digest = request_fingerprint_from_payload(payload)
    return _sanitize_workspace_key(f"fp{REQUEST_FINGERPRINT_SCHEMA_VERSION}_{digest}")


def _legacy_workspace_key(
    mode: str,
    length_ft: float,
    width_ft: float,
    target_ppfd: float,
    peak_capping_enabled: bool,
    hps_coverage_ft: float,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    sim_mode: str = "standard",
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
) -> str:
    length_ft, width_ft = _aligned_dims_ft(length_ft, width_ft)
    normalized_mode = _normalize_mode(mode)
    mode_slug = normalized_mode.lower().replace(" ", "_")
    target_slug = f"{int(round(target_ppfd))}" if abs(target_ppfd - round(target_ppfd)) < 1e-6 else f"{target_ppfd:.2f}".replace(".", "_")
    parts = [f"{mode_slug}_{int(round(length_ft))}x{int(round(width_ft))}_t{target_slug}"]
    parts.append("cap1" if peak_capping_enabled else "cap0")
    if normalized_mode == HPS_MODE_LABEL:
        mount_height = hps_z_m
    elif normalized_mode == MODE_COMPETITOR:
        mount_height = sp_z_m
    else:
        mount_height = mount_z_m
    mount_in = mount_height / 0.0254
    mount_slug = f"{int(round(mount_in))}" if abs(mount_in - round(mount_in)) < 1e-6 else f"{mount_in:.2f}".replace(".", "_")
    parts.append(f"mh{mount_slug}")
    if normalized_mode == HPS_MODE_LABEL:
        coverage_slug = f"{int(round(hps_coverage_ft))}" if abs(hps_coverage_ft - round(hps_coverage_ft)) < 1e-6 else f"{hps_coverage_ft:.2f}".replace(".", "_")
        parts.append(f"hps{coverage_slug}")
        parts.append(f"ies{normalize_hps_ies_variant(hps_ies_variant)}")
    if normalized_mode == MODE_COMPETITOR:
        layout_slug = "practical" if canonical_competitor_layout(competitor_layout) == COMPETITOR_LAYOUT_PRACTICAL else "full"
        parts.append(f"led{layout_slug}")
    if normalized_mode == MODE_SMD:
        parts.append(f"bb{canonicalize_basis_backend(basis_backend)}")
    parts.append(f"exec{canonicalize_execution_mode(execution_mode)}")
    parts.append(f"q{canonicalize_quality_preset(sim_mode)}")
    return _sanitize_session_id("_".join(parts))


def _canonical_number(name: str, value: Any) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise HTTPException(status_code=400, detail=f"Non-finite request value for {name}.")
    decimal = Decimal(str(number))
    if decimal == 0:
        return "0"
    normalized = decimal.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _canonical_bool(value: Any) -> bool:
    return bool(value)


def _canonical_int(name: str, value: Any) -> int:
    if isinstance(value, float) and not math.isfinite(value):
        raise HTTPException(status_code=400, detail=f"Non-finite request value for {name}.")
    return int(value)


def canonical_request_fingerprint_payload(source: Any) -> dict[str, object]:
    getter = source.get if isinstance(source, dict) else lambda name, default=None: getattr(source, name, default)
    mode = _normalize_mode(str(getter("mode", MODE_SMD)))
    length_ft, width_ft = _aligned_dims_ft(
        float(getter("length_ft", 10.0)),
        float(getter("width_ft", 10.0)),
    )
    competitor_layout = str(getter("competitor_layout", COMPETITOR_LAYOUT_FULL))
    if mode == MODE_COMPETITOR:
        competitor_layout = canonical_competitor_layout(competitor_layout)
    basis_backend = str(getter("basis_backend", DEFAULT_SMD_BASIS_BACKEND))
    if mode == MODE_SMD:
        basis_backend = canonicalize_basis_backend(basis_backend)
    hps_ies_variant = str(getter("hps_ies_variant", DEFAULT_HPS_IES_VARIANT))
    if mode == HPS_MODE_LABEL:
        hps_ies_variant = normalize_hps_ies_variant(hps_ies_variant)
    payload: dict[str, object] = {
        "schema_version": REQUEST_FINGERPRINT_SCHEMA_VERSION,
        "fields": {
            "mode": mode,
            "execution_mode": canonicalize_execution_mode(str(getter("execution_mode", DEFAULT_EXECUTION_MODE))),
            "length_ft": _canonical_number("length_ft", length_ft),
            "width_ft": _canonical_number("width_ft", width_ft),
            "target_ppfd": _canonical_number("target_ppfd", getter("target_ppfd", 1000.0)),
            "peak_capping_enabled": _canonical_bool(getter("peak_capping_enabled", False)),
            "run_basis": _canonical_bool(getter("run_basis", True)),
            "w_min": _canonical_number("w_min", getter("w_min", 0.0)),
            "w_max": _canonical_number("w_max", getter("w_max", 100.0)),
            "sim_mode": canonicalize_quality_preset(str(getter("sim_mode", "standard"))),
            "subpatch_grid": _canonical_int("subpatch_grid", getter("subpatch_grid", 1)),
            "mount_z_m": _canonical_number("mount_z_m", getter("mount_z_m", 0.4572)),
            "overlay": overlay_for_mode(mode, str(getter("overlay", "auto"))),
            "smd_base_ring": _canonical_int("smd_base_ring", getter("smd_base_ring", 0)),
            "basis_backend": basis_backend,
            "match_system_ppe": _canonical_bool(getter("match_system_ppe", False)),
            "sp_ppf": _canonical_number("sp_ppf", getter("sp_ppf", COMPETITOR_FIXTURE_PPF_UMOL_S)),
            "sp_z_m": _canonical_number("sp_z_m", getter("sp_z_m", 0.4572)),
            "sp_ppe": _canonical_number("sp_ppe", getter("sp_ppe", COMPETITOR_FIXTURE_PPE_UMOL_PER_J)),
            "competitor_layout": competitor_layout,
            "hps_coverage_ft": _canonical_number("hps_coverage_ft", getter("hps_coverage_ft", 4.0)),
            "hps_z_m": _canonical_number("hps_z_m", getter("hps_z_m", DEFAULT_HPS_MOUNT_Z_M)),
            "hps_fixture_ppf": _canonical_number("hps_fixture_ppf", getter("hps_fixture_ppf", DEFAULT_HPS_FIXTURE_PPF)),
            "hps_input_watts": _canonical_number("hps_input_watts", getter("hps_input_watts", DEFAULT_HPS_INPUT_WATTS)),
            "hps_ies_variant": hps_ies_variant,
            "dialux_sensor_grid": _canonical_bool(getter("dialux_sensor_grid", False)),
        },
    }
    fields = payload["fields"]
    if not isinstance(fields, dict):
        raise RuntimeError("Invalid request fingerprint payload.")
    missing = set(REQUEST_FINGERPRINT_FIELDS) - set(fields)
    if missing:
        raise RuntimeError(f"Request fingerprint payload missing fields: {sorted(missing)}")
    if _canonical_bool(getter("plants_enabled", False)):
        plant_config = plant_geometry_config_from_request(source)
        fields.update(
            {
                "plants_enabled": True,
                "plant_seed": plant_config.seed,
                "plant_rows": plant_config.plant_grid_rows,
                "plant_columns": plant_config.plant_grid_columns,
                "plant_spacing_m": _canonical_number(
                    "plant_spacing_m",
                    plant_config.plant_spacing_m,
                ),
                "plant_height_m": _canonical_number(
                    "plant_height_m",
                    plant_config.plant_height_m,
                ),
                "plant_canopy_radius_m": _canonical_number(
                    "plant_canopy_radius_m",
                    plant_config.canopy_radius_m,
                ),
                "plant_leaf_count": plant_config.leaf_count_per_plant,
                "plant_growth_stage": _canonical_number(
                    "plant_growth_stage",
                    plant_config.growth_stage,
                ),
                "fspm_receiver_granularity": str(getter("fspm_receiver_granularity", "")),
                "fspm_leaf_optical_profile_id": str(getter("fspm_leaf_optical_profile_id", "")),
                "fspm_leaf_radiance_material_mode": str(
                    getter("fspm_leaf_radiance_material_mode", "")
                ),
                "fspm_spectral_transport_mode": str(getter("fspm_spectral_transport_mode", "")),
                "fspm_target_ppfd_umol_m2_s": _canonical_number(
                    "fspm_target_ppfd_umol_m2_s",
                    resolve_fspm_target_ppfd(
                        getter("fspm_target_ppfd_umol_m2_s", None),
                        fallback_target_ppfd=getter("target_ppfd", None),
                    ),
                ),
                "fspm_target_tolerance_umol_m2_s": _canonical_number(
                    "fspm_target_tolerance_umol_m2_s",
                    resolve_fspm_target_tolerance(
                        getter("fspm_target_tolerance_umol_m2_s", None)
                    ),
                ),
            }
        )
    return payload


def canonical_request_fingerprint_json(source: Any) -> str:
    return json.dumps(canonical_request_fingerprint_payload(source), sort_keys=True, separators=(",", ":"))


def request_fingerprint_from_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def request_fingerprint(source: Any) -> str:
    return request_fingerprint_from_payload(canonical_request_fingerprint_payload(source))


def _readable_request_prefix(payload: dict[str, object]) -> str:
    fields = payload["fields"]
    if not isinstance(fields, dict):
        raise RuntimeError("Invalid request fingerprint payload.")
    mode_slug = str(fields["mode"]).lower().replace(" ", "_")
    length = str(fields["length_ft"]).replace(".", "p")
    width = str(fields["width_ft"]).replace(".", "p")
    target = str(fields["target_ppfd"]).replace(".", "p")
    return _sanitize_session_id(f"{mode_slug}_{length}x{width}_t{target}")


def artifact_key_from_request(req: Any) -> str:
    return _workspace_key_for_request(req)


def legacy_artifact_key_from_request(req: Any) -> str:
    return _legacy_workspace_key(
        req.mode,
        req.length_ft,
        req.width_ft,
        req.target_ppfd,
        req.peak_capping_enabled,
        req.hps_coverage_ft,
        req.competitor_layout,
        req.sim_mode,
        req.execution_mode,
        req.mount_z_m,
        req.sp_z_m,
        req.hps_z_m,
        req.hps_ies_variant,
        req.basis_backend,
    )


def _record_root(workspace_key: str) -> Path:
    return CONTENT_ARTIFACTS / _sanitize_workspace_key(workspace_key)


def _committed_workspace_root(workspace_key: str) -> Path:
    return _record_root(workspace_key) / "committed" / "workspace"


def _state_path(record_root: Path) -> Path:
    return record_root / WORKSPACE_STATE_FILENAME


def _integrity_path(record_root: Path) -> Path:
    return record_root / WORKSPACE_INTEGRITY_FILENAME


def _lock_path(record_root: Path) -> Path:
    return record_root / ".lock"


@contextmanager
def _workspace_lock(record_root: Path, *, blocking: bool = True) -> Iterator[bool]:
    record_root.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(record_root)
    with lock_path.open("a+b") as handle:
        try:
            import fcntl

            flags = 0 if blocking else fcntl.LOCK_NB
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | flags)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _workspace_key_from_payload(payload: dict[str, object]) -> str:
    return _sanitize_workspace_key(
        f"fp{REQUEST_FINGERPRINT_SCHEMA_VERSION}_{request_fingerprint_from_payload(payload)}"
    )


def _workspace_key_for_request(req: Any) -> str:
    return _workspace_key_from_payload(canonical_request_fingerprint_payload(req))


def _base_state(
    *,
    state: WorkspaceState,
    workspace_key: str,
    fingerprint: str,
    request_payload: dict[str, object],
) -> dict[str, object]:
    now = time.time()
    return {
        "schema_version": WORKSPACE_RECORD_SCHEMA_VERSION,
        "state": state.value,
        "workspace_key": workspace_key,
        "fingerprint": fingerprint,
        "readable_prefix": _readable_request_prefix(request_payload),
        "request_payload": request_payload,
        "created_at": now,
        "updated_at": now,
    }


def _write_state(record_root: Path, state: dict[str, object], new_state: WorkspaceState) -> None:
    state["state"] = new_state.value
    state["updated_at"] = time.time()
    _atomic_write_json(_state_path(record_root), state)


def _ensure_workspace_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ies_sources").mkdir(parents=True, exist_ok=True)
    (root / "runtime_state").mkdir(parents=True, exist_ok=True)


def _session_grant_path(session_id: str, fingerprint: str) -> Path:
    return _session_root(session_id) / "grants" / f"{fingerprint}.json"


def _secret_bytes() -> bytes:
    ARTIFACT_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _workspace_lock(ARTIFACT_SECRET_PATH.parent):
        if not ARTIFACT_SECRET_PATH.exists():
            ARTIFACT_SECRET_PATH.write_text(secrets.token_urlsafe(48), encoding="ascii")
            ARTIFACT_SECRET_PATH.chmod(0o600)
        return ARTIFACT_SECRET_PATH.read_text(encoding="ascii").strip().encode("ascii")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_payload(payload: dict[str, object]) -> str:
    encoded = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret_bytes(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"v{ARTIFACT_TOKEN_SCHEMA_VERSION}.{encoded}.{_b64url_encode(sig)}"


def _verify_token(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != f"v{ARTIFACT_TOKEN_SCHEMA_VERSION}":
        return None
    expected = hmac.new(_secret_bytes(), parts[1].encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _b64url_decode(parts[2])
    except Exception:
        return None
    if not hmac.compare_digest(expected, supplied):
        return None
    try:
        data = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def issue_artifact_token(session_id: str, req: Any) -> str:
    safe_session = _sanitize_session_id(session_id)
    fingerprint = request_fingerprint(req)
    token = _sign_payload(
        {
            "session_id": safe_session,
            "fingerprint": fingerprint,
            "nonce": secrets.token_urlsafe(18),
            "issued_at": time.time(),
        }
    )
    grant_path = _session_grant_path(safe_session, fingerprint)
    _atomic_write_json(
        grant_path,
        {
            "schema_version": ARTIFACT_TOKEN_SCHEMA_VERSION,
            "session_id": safe_session,
            "fingerprint": fingerprint,
            "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "issued_at": time.time(),
        },
    )
    return token


def _validate_artifact_token(session_id: str, req: Any, token: str | None) -> None:
    if not token:
        raise HTTPException(status_code=403, detail="Artifact token is required.")
    safe_session = _sanitize_session_id(session_id)
    expected_fingerprint = request_fingerprint(req)
    payload = _verify_token(token)
    if payload is None:
        raise HTTPException(status_code=403, detail="Invalid artifact token.")
    if payload.get("session_id") != safe_session or payload.get("fingerprint") != expected_fingerprint:
        raise HTTPException(status_code=403, detail="Artifact token does not match this session and request.")
    grant = _read_json(_session_grant_path(safe_session, expected_fingerprint))
    if not grant:
        raise HTTPException(status_code=403, detail="Artifact grant has expired.")
    token_sha = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if grant.get("token_sha256") != token_sha:
        raise HTTPException(status_code=403, detail="Artifact grant has expired.")


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _workspace_manifest(workspace_root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(p for p in workspace_root.rglob("*") if p.is_file()):
        stat = path.stat()
        files.append(
            {
                "path": str(path.relative_to(workspace_root)),
                "bytes": stat.st_size,
                "sha256": _file_digest(path),
            }
        )
    return {
        "schema_version": WORKSPACE_RECORD_SCHEMA_VERSION,
        "generated_at": time.time(),
        "files": files,
    }


def _validate_workspace_manifest(workspace_root: Path, manifest: dict[str, object]) -> bool:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        rel = str(entry.get("path", "") or "")
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            return False
        path = workspace_root / rel
        if not path.is_file():
            return False
        stat = path.stat()
        if int(entry.get("bytes", -1)) != stat.st_size:
            return False
        if str(entry.get("sha256", "")) != _file_digest(path):
            return False
    return True


def _workspace_integrity_current(record_root: Path, workspace_root: Path) -> bool:
    manifest = _read_json(_integrity_path(record_root))
    return bool(manifest and _validate_workspace_manifest(workspace_root, manifest))


def _state_runtime_identity(state: dict[str, object]) -> object:
    return state.get("runtime_identity")


def allocate_workspace_for_run(session_id: str, req: Any) -> WorkspaceLease:
    safe_session = _sanitize_session_id(session_id)
    payload = canonical_request_fingerprint_payload(req)
    fingerprint = request_fingerprint_from_payload(payload)
    workspace_key = _workspace_key_from_payload(payload)
    record_root = _record_root(workspace_key)
    staging_root = STAGING_ARTIFACTS / workspace_key / secrets.token_urlsafe(16)
    staging_workspace = staging_root / "workspace"
    committed_workspace = _committed_workspace_root(workspace_key)
    _ensure_workspace_dirs(staging_workspace)
    with _workspace_lock(record_root):
        state = _read_json(_state_path(record_root))
        if state is None:
            state = _base_state(
                state=WorkspaceState.ALLOCATED,
                workspace_key=workspace_key,
                fingerprint=fingerprint,
                request_payload=payload,
            )
            _atomic_write_json(_state_path(record_root), state)
        state["active_stage"] = str(staging_root)
        _write_state(record_root, state, WorkspaceState.RUNNING)
    token = issue_artifact_token(safe_session, req)
    return WorkspaceLease(
        session_id=safe_session,
        workspace_key=workspace_key,
        fingerprint=fingerprint,
        request_payload=payload,
        record_root=record_root,
        staging_root=staging_root,
        staging_workspace=staging_workspace,
        committed_workspace=committed_workspace,
        artifact_token=token,
    )


def _last_run_payload(req: Any, runtime_identity: dict[str, object]) -> dict[str, object]:
    return {
        "mode": req.mode,
        "length_ft": req.length_ft,
        "width_ft": req.width_ft,
        "target_ppfd": req.target_ppfd,
        "competitor_layout": req.competitor_layout,
        "hps_coverage_ft": req.hps_coverage_ft,
        "sim_mode": req.sim_mode,
        "execution_mode": req.execution_mode,
        "basis_backend": req.basis_backend,
        "runtime_identity": runtime_identity,
    }


def _required_workspace_outputs(req: Any) -> tuple[str, ...]:
    action = str(getattr(req, "action", "")).strip().lower()
    if action == "visualize":
        return ()
    return ("ppfd_map.txt",)


def _can_reuse_committed_workspace(req: Any | None) -> bool:
    if req is None:
        return True
    execution_mode = canonicalize_execution_mode(
        getattr(req, "execution_mode", DEFAULT_EXECUTION_MODE)
    )
    return execution_mode == EXECUTION_MODE_PRECOMPUTED


def commit_staged_workspace(lease: WorkspaceLease, runtime_identity: dict[str, object], req: Any | None = None) -> None:
    request_obj = req
    with _workspace_lock(lease.record_root):
        state = _read_json(_state_path(lease.record_root))
        if state is None:
            state = _base_state(
                state=WorkspaceState.ALLOCATED,
                workspace_key=lease.workspace_key,
                fingerprint=lease.fingerprint,
                request_payload=lease.request_payload,
            )
        _write_state(lease.record_root, state, WorkspaceState.STAGED)
        if not lease.staging_workspace.exists():
            state["failure_reason"] = "staging workspace missing"
            _write_state(lease.record_root, state, WorkspaceState.FAILED)
            raise RuntimeError("Staging workspace missing.")
        if request_obj is not None:
            for relpath in _required_workspace_outputs(request_obj):
                if not (lease.staging_workspace / relpath).is_file():
                    state["failure_reason"] = f"required output missing: {relpath}"
                    _write_state(lease.record_root, state, WorkspaceState.FAILED)
                    raise RuntimeError(f"Required workspace output missing: {relpath}")
        manifest = _workspace_manifest(lease.staging_workspace)
        if not _validate_workspace_manifest(lease.staging_workspace, manifest):
            state["failure_reason"] = "integrity manifest verification failed"
            _write_state(lease.record_root, state, WorkspaceState.FAILED)
            raise RuntimeError("Workspace integrity verification failed.")
        _write_state(lease.record_root, state, WorkspaceState.VERIFIED)
        if (
            _can_reuse_committed_workspace(request_obj)
            and lease.committed_workspace.exists()
            and _workspace_integrity_current(lease.record_root, lease.committed_workspace)
            and _state_runtime_identity(state) == runtime_identity
        ):
            shutil.rmtree(lease.staging_root, ignore_errors=True)
        else:
            committed_parent = lease.committed_workspace.parent
            committed_parent.mkdir(parents=True, exist_ok=True)
            if lease.committed_workspace.exists():
                quarantine = lease.record_root / "quarantine" / f"workspace_{int(time.time() * 1000)}"
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                os.replace(lease.committed_workspace, quarantine)
            os.replace(lease.staging_workspace, lease.committed_workspace)
            shutil.rmtree(lease.staging_root, ignore_errors=True)
            _atomic_write_json(_integrity_path(lease.record_root), manifest)
        if request_obj is not None:
            last_run = _last_run_payload(request_obj, runtime_identity)
            _atomic_write_json(lease.committed_workspace / "runtime_state" / WORKSPACE_LAST_RUN_FILENAME, last_run)
        state["runtime_identity"] = runtime_identity
        state["committed_at"] = time.time()
        _write_state(lease.record_root, state, WorkspaceState.COMMITTED)


def fail_staged_workspace(lease: WorkspaceLease, reason: str) -> None:
    with _workspace_lock(lease.record_root):
        state = _read_json(_state_path(lease.record_root))
        if state is None:
            state = _base_state(
                state=WorkspaceState.ALLOCATED,
                workspace_key=lease.workspace_key,
                fingerprint=lease.fingerprint,
                request_payload=lease.request_payload,
            )
        state["failure_reason"] = reason
        state["failed_stage"] = str(lease.staging_root)
        _write_state(lease.record_root, state, WorkspaceState.FAILED)


def _artifact_token_from_request(request: Any) -> str | None:
    query_value = request.query_params.get("artifact_token") if request is not None else None
    header_value = request.headers.get("x-radiance-artifact-token") if request is not None else None
    return query_value or header_value


def authorize_workspace_from_request(request: Any, req: Any) -> Path:
    session_id = _session_id_from_request(request)
    _validate_artifact_token(session_id, req, _artifact_token_from_request(request))
    workspace_key = _workspace_key_for_request(req)
    record_root = _record_root(workspace_key)
    workspace_root = _committed_workspace_root(workspace_key)
    with _workspace_lock(record_root):
        state = _read_json(_state_path(record_root))
        if not state or state.get("state") != WorkspaceState.COMMITTED.value:
            raise HTTPException(status_code=404, detail="Committed workspace not found.")
        if not workspace_root.exists():
            raise HTTPException(status_code=404, detail="Committed workspace not found.")
        if not _workspace_integrity_current(record_root, workspace_root):
            raise HTTPException(status_code=409, detail="Workspace integrity check failed.")
    return workspace_root


def _workspace_root(session_id: str, workspace_key: str = "default") -> Path:
    del session_id
    root = _committed_workspace_root(workspace_key)
    _ensure_workspace_dirs(root)
    return root


def _workspace_root_for_request(session_id: str, req: Any) -> Path:
    return _workspace_root(session_id, artifact_key_from_request(req))


def _workspace_root_for_params(
    session_id: str,
    mode: str,
    length_ft: float,
    width_ft: float,
    target_ppfd: float,
    peak_capping_enabled: bool,
    hps_coverage_ft: float,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    sim_mode: str = "standard",
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
    run_basis: bool = True,
    w_min: float = 0.0,
    w_max: float = 100.0,
    subpatch_grid: int = 1,
    overlay: str = "auto",
    smd_base_ring: int = 0,
    match_system_ppe: bool = False,
    sp_ppf: float = COMPETITOR_FIXTURE_PPF_UMOL_S,
    sp_ppe: float = COMPETITOR_FIXTURE_PPE_UMOL_PER_J,
    hps_fixture_ppf: float = DEFAULT_HPS_FIXTURE_PPF,
    hps_input_watts: float = DEFAULT_HPS_INPUT_WATTS,
    dialux_sensor_grid: bool = False,
) -> Path:
    return _workspace_root(
        session_id,
        _workspace_key(
            mode,
            length_ft,
            width_ft,
            target_ppfd,
            peak_capping_enabled,
            hps_coverage_ft,
            competitor_layout,
            sim_mode,
            execution_mode,
            mount_z_m,
            sp_z_m,
            hps_z_m,
            hps_ies_variant,
            basis_backend,
            run_basis,
            w_min,
            w_max,
            subpatch_grid,
            overlay,
            smd_base_ring,
            match_system_ppe,
            sp_ppf,
            sp_ppe,
            hps_fixture_ppf,
            hps_input_watts,
            dialux_sensor_grid,
        ),
    )


def _workspace_last_run_path(session_id: str, req: Any) -> Path:
    return _workspace_root_for_request(session_id, req) / "runtime_state" / WORKSPACE_LAST_RUN_FILENAME


def prune_workspace_store(
    *,
    now: float | None = None,
    retention_s: int = WORKSPACE_STAGING_RETENTION_S,
    max_records: int = WORKSPACE_CLEANUP_MAX_RECORDS,
) -> dict[str, int]:
    current = time.time() if now is None else now
    metrics = {"scanned": 0, "expired": 0, "locked": 0, "removed_staging": 0}
    if not CONTENT_ARTIFACTS.exists():
        return metrics
    for record_root in sorted((p for p in CONTENT_ARTIFACTS.iterdir() if p.is_dir()), key=lambda p: p.name):
        if metrics["scanned"] >= max_records:
            break
        metrics["scanned"] += 1
        with _workspace_lock(record_root, blocking=False) as locked:
            if not locked:
                metrics["locked"] += 1
                continue
            state = _read_json(_state_path(record_root))
            if not state:
                continue
            updated_raw = state.get("updated_at", 0.0)
            updated = float(updated_raw) if isinstance(updated_raw, (int, float, str)) else 0.0
            stale = (current - updated) >= retention_s
            if not stale:
                continue
            state_value = str(state.get("state", "") or "")
            if state_value in {WorkspaceState.RUNNING.value, WorkspaceState.STAGED.value, WorkspaceState.FAILED.value}:
                active_stage = state.get("active_stage") or state.get("failed_stage")
                if isinstance(active_stage, str) and active_stage:
                    stage_path = Path(active_stage)
                    if stage_path.exists() and STAGING_ARTIFACTS in stage_path.parents:
                        shutil.rmtree(stage_path, ignore_errors=True)
                        metrics["removed_staging"] += 1
                _write_state(record_root, state, WorkspaceState.EXPIRED)
                metrics["expired"] += 1
    return metrics
