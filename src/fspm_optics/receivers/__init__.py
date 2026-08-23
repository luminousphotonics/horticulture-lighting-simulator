"""Receiver sampling, parsing, aggregation, and artifacts."""

from .aggregation import (
    BASELINE_PPFD_PROXY_METHOD,
    PLANT_SURFACE_FLUX_SCHEMA,
    RADIANCE_RECEIVER_METHOD,
    SPATIAL_PPFD_PROXY_METHOD,
    build_baseline_proxy_surface_flux_rows,
    build_plant_surface_flux_payload,
    build_radiance_receiver_surface_flux_rows,
    build_spatial_proxy_surface_flux_rows,
    read_ppfd_map_field,
)
from .artifacts import (
    build_patch_receiver_artifact_payload,
    compact_plant_surface_flux_payload,
    write_baseline_proxy_plant_surface_flux_artifact,
    write_plant_surface_flux_artifact,
    write_radiance_receiver_plant_surface_flux_artifact,
    write_spatial_proxy_plant_surface_flux_artifact,
)
from .parsing import parse_rtrace_receiver_output
from .samples import (
    DEFAULT_FSPM_RECEIVER_GRANULARITY,
    MeshPatchReceiverSample,
    RECEIVER_GRANULARITY_LEAF_CENTROID,
    RECEIVER_GRANULARITY_LEAF_QUADRATURE_4,
    RECEIVER_GRANULARITY_MESH_PATCH,
    build_radiance_receiver_samples,
    build_two_sided_patch_receivers,
    normalize_receiver_granularity,
    receiver_granularity_role,
    receiver_sample_input_text,
)

__all__ = [name for name in globals() if not name.startswith("_")]
