from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


def clone_authenticated_workspace(
    template: Path,
    destination: Path,
    *,
    rebase_external_parent: bool = False,
) -> Path:
    """Copy an immutable workspace and rebase path-bearing text to its private root."""

    source = template.resolve()
    target = destination.resolve()
    if not source.is_dir():
        raise ValueError(f"workspace template does not exist: {source}")
    if target.exists():
        raise ValueError(f"private workspace destination already exists: {target}")
    if rebase_external_parent and source.name != target.name:
        raise ValueError(
            "external-parent rebasing requires the workspace name to stay fixed"
        )

    shutil.copytree(source, target)
    path_mappings = [(source, target)]
    if rebase_external_parent:
        path_mappings.insert(0, (source.parent, target.parent))
    path_bearing_text = (
        *target.rglob("*.json"),
        *target.rglob("fixture_body_instances.rad"),
    )
    for path in path_bearing_text:
        payload = path.read_bytes()
        rebased = payload
        for old_root, new_root in path_mappings:
            rebased = rebased.replace(
                str(old_root).encode("utf-8"),
                str(new_root).encode("utf-8"),
            )
        if rebased != payload:
            path.write_bytes(rebased)
    for metadata_path in target.rglob("fixture_occlusion.json"):
        instance_path = metadata_path.with_name("fixture_body_instances.rad")
        if not instance_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["instance_source_sha256"] = hashlib.sha256(
            instance_path.read_bytes()
        ).hexdigest()
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return target


def clone_five_band_workspace(template: Path, destination: Path) -> Path:
    """Clone Phase 19 and reauthenticate its path-bearing transport manifest."""

    target = clone_authenticated_workspace(template, destination)
    artifact_root = target / "rex_five_band"
    plan_path = artifact_root / "five_band_transport_plan.json"
    summary_path = artifact_root / "five_band_receiver_summary.json"
    plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["transport_plan_identity"]["manifest_sha256"] = plan_sha256
    summary["shared_input_hashes"]["transport_plan_sha256"] = plan_sha256
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
