from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from rad_rebuild.radiance.backend.models import DockerStatusResponse
from rad_rebuild.radiance.backend.runner import docker_status_payload
from rad_rebuild.radiance.backend.runtime import precomputed_mode
from rad_rebuild.radiance.backend.routes.contracts import PUBLIC_ERROR_RESPONSES
from rad_rebuild.radiance.backend.workspace import ROOT

try:
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import resolve_precomputed_root
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import Docker route dependencies: {e}") from e


router = APIRouter()


@router.get(
    "/docker/status",
    response_model=DockerStatusResponse,
    status_code=200,
    operation_id="get_docker_status",
    responses=PUBLIC_ERROR_RESPONSES,
)
def docker_status() -> dict[str, Any]:
    return docker_status_payload(precomputed_mode(), resolve_precomputed_root(ROOT))
