"""Deterministic SMD emitter artifact payloads and thin file writers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .radiance_writer import SmdRadianceDocument


@dataclass(frozen=True, slots=True)
class SmdEmitterArtifactPaths:
    radiance_path: Path
    metadata_path: Path
    source_variant_cal_path: Path | None


def build_smd_emitter_artifact_payload(
    document: SmdRadianceDocument,
) -> dict[str, Any]:
    """Return deterministic metadata without hashes or timestamps."""

    metadata = asdict(document.metadata)
    return {
        "schema_version": 2,
        "artifact_type": "fspm_optics_smd_emitters",
        "metadata": metadata,
        "basis_inputs": {
            "module_profile_id": document.metadata.module_profile_id,
            "module_count": document.metadata.module_count,
            "control_zone_count": document.metadata.control_zone_count,
            "control_zone_ids": document.metadata.control_zone_ids,
            "watts_by_control_zone": document.metadata.watts_by_control_zone,
            "solver_controlled_electrical_watts_by_zone": (
                document.metadata.watts_by_control_zone
            ),
            "internal_source_ppe_umol_per_j": (
                document.metadata.internal_source_ppe_umol_per_j
            ),
            "completed_aperture_fixture_ppe_umol_per_j": (
                document.metadata.completed_aperture_fixture_ppe_umol_per_j
            ),
            "accepted_fixture_transmission": (
                document.metadata.accepted_fixture_transmission
            ),
            "optical_stack_id": document.metadata.optical_stack_id,
            "source_variant": document.metadata.source_variant,
            "scalar_par_carrier_basis": document.metadata.scalar_par_carrier_basis,
        },
    }


def format_smd_emitter_metadata_json(document: SmdRadianceDocument) -> str:
    return json.dumps(
        build_smd_emitter_artifact_payload(document),
        indent=2,
        sort_keys=True,
    ) + "\n"


def write_smd_emitter_artifacts(
    document: SmdRadianceDocument,
    *,
    radiance_path: str | Path,
    metadata_path: str | Path,
) -> SmdEmitterArtifactPaths:
    """Write a prebuilt pure document and its metadata to local files."""

    radiance_output = Path(radiance_path)
    metadata_output = Path(metadata_path)
    radiance_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    radiance_output.write_text(document.radiance_text, encoding="utf-8")
    metadata_output.write_text(
        format_smd_emitter_metadata_json(document), encoding="utf-8"
    )
    cal_output: Path | None = None
    if document.source_variant_cal_text is not None:
        cal_output = radiance_output.with_name("smd_source_variant.cal")
        cal_output.write_text(document.source_variant_cal_text, encoding="utf-8")
    return SmdEmitterArtifactPaths(
        radiance_path=radiance_output,
        metadata_path=metadata_output,
        source_variant_cal_path=cal_output,
    )
