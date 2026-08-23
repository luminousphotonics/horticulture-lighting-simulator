"""JSON payload and artifact writers for receiver aggregation results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, overload

from fspm_optics.fspm.targets import (
    FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY,
    FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL,
    FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP,
    FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP,
)
from fspm_optics.optics.leaf_materials import opaque_leaf_material_metadata
from fspm_optics.plants.absorption import leaf_absorption_surfaces
from fspm_optics.plants.models import PlantMesh, PlantScene
from fspm_optics.receivers.aggregation import (
    BASELINE_PPFD_PROXY_METHOD,
    RADIANCE_RECEIVER_METHOD,
    SPATIAL_PPFD_PROXY_METHOD,
    build_baseline_proxy_surface_flux_rows,
    build_plant_surface_flux_payload,
    build_radiance_receiver_surface_flux_rows,
    build_spatial_proxy_surface_flux_rows,
    read_ppfd_map_field,
)
from fspm_optics.receivers.samples import (
    MeshPatchReceiverSample,
    finite_non_negative,
    normalize_receiver_granularity,
    normal_generation_basis,
    receiver_area_basis,
    receiver_generation_basis,
    receiver_granularity_role,
    receiver_side_policy,
    surface_geometry_by_id,
)

PLANT_SURFACE_FLUX_FILENAME = "plant_surface_flux.json"


def build_patch_receiver_artifact_payload(
    plant: PlantMesh,
    samples: Iterable[MeshPatchReceiverSample],
) -> dict[str, Any]:
    """Build deterministic scientific receiver geometry metadata."""

    ordered = tuple(samples)
    receiver_ids = tuple(sample.receiver_id for sample in ordered)
    if len(receiver_ids) != len(set(receiver_ids)):
        raise ValueError("Scientific receiver IDs must be unique.")
    if len(ordered) != 2 * plant.patch_count:
        raise ValueError(
            "Scientific receiver count must equal two times the patch count."
        )
    offsets = {sample.normal_offset_m for sample in ordered}
    if len(offsets) != 1:
        raise ValueError("Scientific receivers must use one normal offset.")
    return {
        "schema_version": 1,
        "artifact_type": "fspm_optics_rex_leaf_patch_receivers",
        "plant_id": plant.plant_id,
        "seed": plant.seed,
        "leaf_count": len(plant.leaves),
        "face_count": plant.face_count,
        "patch_count": plant.patch_count,
        "receiver_count": len(ordered),
        "leaf_patch_grid": list(plant.config.leaf_patch_grid),
        "normal_offset_m": next(iter(offsets)),
        "side_policy": "front_and_back_per_mesh_face",
        "receivers": [sample.to_dict() for sample in ordered],
    }


def compact_plant_surface_flux_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove high-volume per-entity rows while retaining aggregate metadata."""

    return {
        key: value
        for key, value in dict(payload).items()
        if key not in {"plant_summaries", "leaf_summaries", "surface_summaries"}
    }


def write_plant_surface_flux_artifact(
    target_dir: str | Path,
    payload: Mapping[str, Any],
    *,
    compact: bool = True,
) -> Path:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / PLANT_SURFACE_FLUX_FILENAME
    output = compact_plant_surface_flux_payload(payload) if compact else dict(payload)
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _classification_from_field(scene: PlantScene, path: str | Path) -> dict[str, float]:
    field = read_ppfd_map_field(path)
    return {
        surface_id: field.sample(item["centroid_m"][0], item["centroid_m"][1])
        for surface_id, item in surface_geometry_by_id(scene).items()
    }


@overload
def write_radiance_receiver_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    return_payload: Literal[True],
    **kwargs: Any,
) -> tuple[Path, dict[str, Any]]: ...


@overload
def write_radiance_receiver_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    return_payload: Literal[False] = False,
    **kwargs: Any,
) -> Path: ...


def write_radiance_receiver_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    receiver_samples: Iterable[Mapping[str, Any]],
    receiver_flux_density_umol_m2_s: Iterable[float],
    *,
    receiver_scale_multiplier: float = 1.0,
    source_octree: str | None = None,
    receiver_granularity: str | None = None,
    baseline_transport_scene: str = "room_emitters_only",
    fspm_receiver_transport_scene: str = "room_emitters_plants",
    receiver_trace_count: int = 1,
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
    target_classification_ppfd_map_path: str | Path | None = None,
    leaf_material_metadata: Mapping[str, Any] | None = None,
    return_payload: bool = False,
) -> Path | tuple[Path, dict[str, Any]]:
    samples = list(receiver_samples)
    densities = list(receiver_flux_density_umol_m2_s)
    detected = {
        normalize_receiver_granularity(sample.get("receiver_granularity"))
        for sample in samples
    }
    if len(detected) != 1:
        raise ValueError("Receiver samples must use one receiver granularity.")
    granularity = normalize_receiver_granularity(receiver_granularity or next(iter(detected)))
    if detected != {granularity}:
        raise ValueError("receiver_granularity does not match receiver sample metadata.")

    surfaces = leaf_absorption_surfaces(scene)
    leaf_count = sum(len(plant.leaves) for plant in scene.plants)
    represented_area = sum(surface.area_m2 for surface in surfaces)
    sample_area = sum(
        finite_non_negative(f"receiver sample area {index}", sample.get("area_m2"))
        for index, sample in enumerate(samples)
    )
    rows = build_radiance_receiver_surface_flux_rows(
        scene,
        samples,
        densities,
        receiver_scale_multiplier=receiver_scale_multiplier,
    )
    classification = (
        _classification_from_field(scene, target_classification_ppfd_map_path)
        if target_classification_ppfd_map_path is not None
        else None
    )
    metadata = dict(leaf_material_metadata or opaque_leaf_material_metadata())
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=RADIANCE_RECEIVER_METHOD,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        target_classification_ppfd_by_surface_id=classification,
        leaf_material_metadata=metadata,
        receiver_sample_count=len(samples),
        receiver_granularity=granularity,
        receiver_samples_per_leaf=len(samples) / leaf_count if leaf_count else 0.0,
        receiver_generation_basis=receiver_generation_basis(granularity),
        receiver_represented_area_m2=represented_area,
        receiver_sample_area_sum_m2=sample_area,
        receiver_area_basis=receiver_area_basis(granularity),
        receiver_side_policy=receiver_side_policy(granularity),
        normal_generation_basis=normal_generation_basis(granularity),
        receiver_granularity_role=receiver_granularity_role(granularity),
        source_octree=source_octree,
        baseline_transport_scene=baseline_transport_scene,
        fspm_receiver_transport_scene=fspm_receiver_transport_scene,
        receiver_trace_count=receiver_trace_count,
    )
    if classification is not None:
        payload.update(
            {
                "target_classification_basis": FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY,
                "target_classification_basis_label": FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL,
                "target_classification_source": FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP,
                "target_classification_note": FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP,
            }
        )
    path = write_plant_surface_flux_artifact(target_dir, payload)
    return (path, payload) if return_payload else path


def write_baseline_proxy_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    *,
    baseline_ppfd_mean_umol_m2_s: float,
    source_ppfd_map: str = "ppfd_map.txt",
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
) -> Path:
    rows = build_baseline_proxy_surface_flux_rows(scene, baseline_ppfd_mean_umol_m2_s)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
        source_ppfd_map=source_ppfd_map,
        baseline_ppfd_mean_umol_m2_s=baseline_ppfd_mean_umol_m2_s,
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
    )
    return write_plant_surface_flux_artifact(target_dir, payload)


def write_spatial_proxy_plant_surface_flux_artifact(
    target_dir: str | Path,
    scene: PlantScene,
    *,
    ppfd_map_path: str | Path,
    source_ppfd_map: str = "ppfd_map.txt",
    target_ppfd_umol_m2_s: float | None = None,
    target_tolerance_umol_m2_s: float | None = None,
) -> Path:
    field = read_ppfd_map_field(ppfd_map_path)
    rows = build_spatial_proxy_surface_flux_rows(scene, ppfd_map_path)
    payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=SPATIAL_PPFD_PROXY_METHOD,
        source_ppfd_map=source_ppfd_map,
        baseline_ppfd_mean_umol_m2_s=field.mean_umol_m2_s,
        ppfd_field_summary=field.summary(),
        target_ppfd_umol_m2_s=target_ppfd_umol_m2_s,
        target_tolerance_umol_m2_s=target_tolerance_umol_m2_s,
        target_classification_ppfd_by_surface_id=_classification_from_field(scene, ppfd_map_path),
    )
    return write_plant_surface_flux_artifact(target_dir, payload)
