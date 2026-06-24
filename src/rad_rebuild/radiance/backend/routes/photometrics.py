from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from rad_rebuild.radiance.backend.env import (
    _canonicalize_mode_request,
    _normalize_mode,
    _request_uses_precomputed,
)
from rad_rebuild.radiance.backend.models import AssemblyPhotometricLayerResponse, RadianceRunRequest
from rad_rebuild.radiance.backend.photometric_layer import (
    PhotometricLayerError,
    build_assembly_photometric_layer,
)
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.runtime import (
    maybe_cleanup_runtime_state,
    precomputed_request_for_available_bundle,
    request_bool_query_param,
)
from rad_rebuild.radiance.backend.workspace import authorize_workspace_from_request
from rad_rebuild.radiance.config import (
    COMPETITOR_LAYOUT_FULL,
    DEFAULT_EXECUTION_MODE,
    MODE_SMD,
    PUBLIC_DEFAULT_LENGTH_FT,
    PUBLIC_DEFAULT_WIDTH_FT,
)

try:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
        DEFAULT_COVERAGE_FT as DEFAULT_HPS_COVERAGE_FT,
        DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
        DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    )
    from rad_rebuild.radiance.engine.simulation.basis_backends import (
        DEFAULT_SMD_BASIS_BACKEND,
        validate_basis_backend_request,
    )
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import canonical_competitor_layout
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import photometric route dependencies: {e}") from e


router = APIRouter()

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _artifact_w_min(mode: str, w_min: float | None) -> float:
    if w_min is not None:
        return w_min
    return 0.0 if mode == MODE_SMD else 10.0


def _route_radiance_request(**kwargs: object) -> RadianceRunRequest:
    try:
        return RadianceRunRequest.model_validate(kwargs)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _public_photometric_error(exc: PhotometricLayerError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.error, "message": exc.message},
    )


def _photometric_request(
    request: Request,
    *,
    mode: str,
    execution_mode: str,
    sim_mode: str,
    target_ppfd: float,
    peak_capping_enabled: bool,
    match_system_ppe: bool,
    length_ft: float,
    width_ft: float,
    w_min: float | None,
    w_max: float,
    competitor_layout: str,
    hps_coverage_ft: float,
    hps_ies_variant: str,
    mount_z_m: float,
    sp_z_m: float,
    hps_z_m: float,
    basis_backend: str,
    plants_enabled: bool,
    plant_seed: int | None,
    plant_rows: int | None,
    plant_columns: int | None,
    plant_spacing_m: float | None,
    plant_height_m: float | None,
    plant_canopy_radius_m: float | None,
    plant_leaf_count: int | None,
    plant_growth_stage: float | None,
) -> RadianceRunRequest:
    mode = _normalize_mode(mode)
    peak_capping_enabled = request_bool_query_param(request, "peak_capping_enabled", peak_capping_enabled)
    match_system_ppe = request_bool_query_param(request, "match_system_ppe", match_system_ppe)
    plants_enabled = request_bool_query_param(request, "plants_enabled", plants_enabled)
    try:
        basis_backend = validate_basis_backend_request(mode, basis_backend, variable_mode="rings")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    competitor_layout = canonical_competitor_layout(competitor_layout)
    req = _canonicalize_mode_request(
        _route_radiance_request(
            action="visualize",
            mode=mode,
            execution_mode=execution_mode,
            sim_mode=sim_mode,
            target_ppfd=target_ppfd,
            peak_capping_enabled=peak_capping_enabled,
            match_system_ppe=match_system_ppe,
            length_ft=length_ft,
            width_ft=width_ft,
            w_min=_artifact_w_min(mode, w_min),
            w_max=w_max,
            competitor_layout=competitor_layout,
            hps_coverage_ft=hps_coverage_ft,
            hps_ies_variant=hps_ies_variant,
            mount_z_m=mount_z_m,
            sp_z_m=sp_z_m,
            hps_z_m=hps_z_m,
            basis_backend=basis_backend,
            plants_enabled=plants_enabled,
            plant_seed=plant_seed,
            plant_rows=plant_rows,
            plant_columns=plant_columns,
            plant_spacing_m=plant_spacing_m,
            plant_height_m=plant_height_m,
            plant_canopy_radius_m=plant_canopy_radius_m,
            plant_leaf_count=plant_leaf_count,
            plant_growth_stage=plant_growth_stage,
        )
    )
    matched_req = precomputed_request_for_available_bundle(req) if _request_uses_precomputed(req) else None
    return matched_req if matched_req is not None else req


def _authorized_layer_payload(
    request: Request,
    *,
    mode: str,
    execution_mode: str,
    sim_mode: str,
    target_ppfd: float,
    peak_capping_enabled: bool,
    match_system_ppe: bool,
    length_ft: float,
    width_ft: float,
    w_min: float | None,
    w_max: float,
    competitor_layout: str,
    hps_coverage_ft: float,
    hps_ies_variant: str,
    mount_z_m: float,
    sp_z_m: float,
    hps_z_m: float,
    basis_backend: str,
    plants_enabled: bool,
    plant_seed: int | None,
    plant_rows: int | None,
    plant_columns: int | None,
    plant_spacing_m: float | None,
    plant_height_m: float | None,
    plant_canopy_radius_m: float | None,
    plant_leaf_count: int | None,
    plant_growth_stage: float | None,
) -> tuple[dict[str, Any], bytes]:
    req = _photometric_request(
        request,
        mode=mode,
        execution_mode=execution_mode,
        sim_mode=sim_mode,
        target_ppfd=target_ppfd,
        peak_capping_enabled=peak_capping_enabled,
            match_system_ppe=match_system_ppe,
        length_ft=length_ft,
        width_ft=width_ft,
        w_min=w_min,
        w_max=w_max,
        competitor_layout=competitor_layout,
        hps_coverage_ft=hps_coverage_ft,
        hps_ies_variant=hps_ies_variant,
        mount_z_m=mount_z_m,
        sp_z_m=sp_z_m,
        hps_z_m=hps_z_m,
        basis_backend=basis_backend,
        plants_enabled=plants_enabled,
        plant_seed=plant_seed,
        plant_rows=plant_rows,
        plant_columns=plant_columns,
        plant_spacing_m=plant_spacing_m,
        plant_height_m=plant_height_m,
        plant_canopy_radius_m=plant_canopy_radius_m,
        plant_leaf_count=plant_leaf_count,
        plant_growth_stage=plant_growth_stage,
    )
    workspace_root = authorize_workspace_from_request(request, req)
    try:
        return build_assembly_photometric_layer(workspace_root, req)
    except PhotometricLayerError as exc:
        raise _public_photometric_error(exc) from exc


@router.get(
    "/radiance/assembly-photometric-layer",
    response_model=AssemblyPhotometricLayerResponse,
    status_code=200,
    operation_id="get_radiance_assembly_photometric_layer",
    responses=PUBLIC_ERROR_RESPONSES,
)
@router.head("/radiance/assembly-photometric-layer", include_in_schema=False)
def radiance_assembly_photometric_layer(
    request: Request,
    response: Response,
    mode: str = MODE_SMD,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float = 1000.0,
    peak_capping_enabled: bool = False,
    match_system_ppe: bool = False,
    length_ft: float = float(PUBLIC_DEFAULT_LENGTH_FT),
    width_ft: float = float(PUBLIC_DEFAULT_WIDTH_FT),
    w_min: float | None = None,
    w_max: float = 100.0,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    hps_coverage_ft: float = DEFAULT_HPS_COVERAGE_FT,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
    plants_enabled: bool = False,
    plant_seed: int | None = None,
    plant_rows: int | None = None,
    plant_columns: int | None = None,
    plant_spacing_m: float | None = None,
    plant_height_m: float | None = None,
    plant_canopy_radius_m: float | None = None,
    plant_leaf_count: int | None = None,
    plant_growth_stage: float | None = None,
    session_id: str | None = None,
    artifact_token: str | None = None,
) -> Any:
    del session_id, artifact_token
    maybe_cleanup_runtime_state()
    metadata, _binary = _authorized_layer_payload(
        request,
        mode=mode,
        execution_mode=execution_mode,
        sim_mode=sim_mode,
        target_ppfd=target_ppfd,
        peak_capping_enabled=peak_capping_enabled,
            match_system_ppe=match_system_ppe,
        length_ft=length_ft,
        width_ft=width_ft,
        w_min=w_min,
        w_max=w_max,
        competitor_layout=competitor_layout,
        hps_coverage_ft=hps_coverage_ft,
        hps_ies_variant=hps_ies_variant,
        mount_z_m=mount_z_m,
        sp_z_m=sp_z_m,
        hps_z_m=hps_z_m,
        basis_backend=basis_backend,
        plants_enabled=plants_enabled,
        plant_seed=plant_seed,
        plant_rows=plant_rows,
        plant_columns=plant_columns,
        plant_spacing_m=plant_spacing_m,
        plant_height_m=plant_height_m,
        plant_canopy_radius_m=plant_canopy_radius_m,
        plant_leaf_count=plant_leaf_count,
        plant_growth_stage=plant_growth_stage,
    )
    if request.method == "HEAD":
        return Response(status_code=200, headers=NO_STORE_HEADERS)
    response.headers.update(NO_STORE_HEADERS)
    return metadata


@router.get(
    "/radiance/assembly-photometric-layer.bin",
    response_class=Response,
    status_code=200,
    operation_id="get_radiance_assembly_photometric_layer_binary",
    responses=PUBLIC_ERROR_RESPONSES,
)
@router.head("/radiance/assembly-photometric-layer.bin", include_in_schema=False)
def radiance_assembly_photometric_layer_binary(
    request: Request,
    mode: str = MODE_SMD,
    execution_mode: str = DEFAULT_EXECUTION_MODE,
    sim_mode: str = "standard",
    target_ppfd: float = 1000.0,
    peak_capping_enabled: bool = False,
    match_system_ppe: bool = False,
    length_ft: float = float(PUBLIC_DEFAULT_LENGTH_FT),
    width_ft: float = float(PUBLIC_DEFAULT_WIDTH_FT),
    w_min: float | None = None,
    w_max: float = 100.0,
    competitor_layout: str = COMPETITOR_LAYOUT_FULL,
    hps_coverage_ft: float = DEFAULT_HPS_COVERAGE_FT,
    hps_ies_variant: str = DEFAULT_HPS_IES_VARIANT,
    mount_z_m: float = 0.4572,
    sp_z_m: float = 0.4572,
    hps_z_m: float = DEFAULT_HPS_MOUNT_Z_M,
    basis_backend: str = DEFAULT_SMD_BASIS_BACKEND,
    plants_enabled: bool = False,
    plant_seed: int | None = None,
    plant_rows: int | None = None,
    plant_columns: int | None = None,
    plant_spacing_m: float | None = None,
    plant_height_m: float | None = None,
    plant_canopy_radius_m: float | None = None,
    plant_leaf_count: int | None = None,
    plant_growth_stage: float | None = None,
    session_id: str | None = None,
    artifact_token: str | None = None,
) -> Response:
    del session_id, artifact_token
    maybe_cleanup_runtime_state()
    _metadata, binary = _authorized_layer_payload(
        request,
        mode=mode,
        execution_mode=execution_mode,
        sim_mode=sim_mode,
        target_ppfd=target_ppfd,
        peak_capping_enabled=peak_capping_enabled,
            match_system_ppe=match_system_ppe,
        length_ft=length_ft,
        width_ft=width_ft,
        w_min=w_min,
        w_max=w_max,
        competitor_layout=competitor_layout,
        hps_coverage_ft=hps_coverage_ft,
        hps_ies_variant=hps_ies_variant,
        mount_z_m=mount_z_m,
        sp_z_m=sp_z_m,
        hps_z_m=hps_z_m,
        basis_backend=basis_backend,
        plants_enabled=plants_enabled,
        plant_seed=plant_seed,
        plant_rows=plant_rows,
        plant_columns=plant_columns,
        plant_spacing_m=plant_spacing_m,
        plant_height_m=plant_height_m,
        plant_canopy_radius_m=plant_canopy_radius_m,
        plant_leaf_count=plant_leaf_count,
        plant_growth_stage=plant_growth_stage,
    )
    if request.method == "HEAD":
        return Response(status_code=200, headers=NO_STORE_HEADERS, media_type="application/octet-stream")
    return Response(
        content=binary,
        media_type="application/octet-stream",
        headers=NO_STORE_HEADERS,
    )
