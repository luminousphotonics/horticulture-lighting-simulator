from __future__ import annotations

import csv
import hashlib
import json
import shlex
from pathlib import Path

from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD
from rad_rebuild.radiance.engine.plants.artifacts import PLANT_ARTIFACT_FILENAMES
from rad_rebuild.radiance.paths import REPO_ROOT
from rad_rebuild.radiance.settings import get_settings

from .env import (
    HPS_MODE_LABEL,
    _apply_visualize_env,
    _normalize_mode,
    _output_dir_for_mode,
    _overlay_for_mode,
    _resolve_output_dir_path,
)
from .models import RadianceRunRequest
from .runner import _local_command, _run_cmd
from .workspace import ENGINE_PACKAGE_ROOT, ROOT

try:
    from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
        calibration_sensitive_elements as hps_calibration_sensitive_elements,
        profile_manifest_dict as hps_profile_manifest_dict,
    )
    from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
        CURVE_MODEL_VERSION as SMD_CURVE_MODEL_VERSION,
    )
    from rad_rebuild.radiance.engine.simulation.basis_backends import basis_backend_request_fields
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import backend artifact dependencies: {e}") from e


RADIANCE_MANIFEST_SCHEMA_VERSION = 2
BACKEND_SERVER_FILE = Path(__file__).with_name("server.py")
FILE_CACHE_MAX_ENTRIES = get_settings().file_cache_max_entries
FILE_SHA256_CACHE: dict[tuple[str, int, int], str] = {}
FILE_ENTRY_CACHE: dict[tuple[str, int, int], dict[str, str | int]] = {}


def _prune_file_caches() -> None:
    if len(FILE_SHA256_CACHE) > FILE_CACHE_MAX_ENTRIES:
        FILE_SHA256_CACHE.clear()
    if len(FILE_ENTRY_CACHE) > FILE_CACHE_MAX_ENTRIES:
        FILE_ENTRY_CACHE.clear()


def _sha256_file(path: Path) -> str:
    stat = path.stat()
    key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    cached = FILE_SHA256_CACHE.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    FILE_SHA256_CACHE[key] = digest
    return digest


def _file_entry(path: Path) -> dict[str, str | int]:
    stat = path.stat()
    key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    cached = FILE_ENTRY_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    entry: dict[str, str | int] = {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256_file(path),
        "bytes": stat.st_size,
    }
    FILE_ENTRY_CACHE[key] = dict(entry)
    return entry


def _portable_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        pass
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_json_file(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _collect_files(paths: list[Path]) -> list[dict[str, str | int]]:
    return [_file_entry(path) for path in paths if path.exists()]


def _ppfd_csv_path(outdir: Path) -> Path:
    return outdir / "ppfd_map.csv"


def _latest_mtime(paths: list[Path | None]) -> int:
    latest = 0
    for path in paths:
        if not path or not path.exists():
            continue
        try:
            latest = max(latest, int(path.stat().st_mtime_ns))
        except Exception:
            continue
    return latest


def _cache_fresh(cache_path: Path, dependencies: list[Path | None]) -> bool:
    if not cache_path.exists():
        return False
    try:
        cache_mtime = int(cache_path.stat().st_mtime_ns)
    except Exception:
        return False
    return cache_mtime >= _latest_mtime(dependencies)


def _write_ppfd_csv(ppfd_txt: Path, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = _ppfd_csv_path(outdir)
    if _cache_fresh(csv_path, [ppfd_txt]):
        return csv_path
    with ppfd_txt.open("r", encoding="utf-8", errors="ignore") as src, csv_path.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        writer = csv.writer(dst)
        writer.writerow(["x_m", "y_m", "z_m", "ppfd"])
        for line in src:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            writer.writerow(parts[:4])
    return csv_path


def _manifest_path(mode: str, workspace_root: Path | None = None) -> Path:
    slug = mode.lower().replace(" ", "_")
    out = (workspace_root or ROOT) / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"radiance_manifest_{slug}.json"


def _generator_file_for_mode(mode: str) -> Path:
    mode = _normalize_mode(mode)
    if mode == MODE_COMPETITOR:
        return ENGINE_PACKAGE_ROOT / "emitters" / "generate_emitters_spydr3.py"
    if mode == HPS_MODE_LABEL:
        return ENGINE_PACKAGE_ROOT / "emitters" / "generate_emitters_hps.py"
    return ENGINE_PACKAGE_ROOT / "emitters" / "generate_emitters_smd.py"


def _emitters_file_for_mode(mode: str, workspace_root: Path | None = None) -> Path | None:
    base_root = workspace_root or ROOT
    mode = _normalize_mode(mode)
    if mode == MODE_COMPETITOR:
        return base_root / "runtime_state" / "emitters_spydr3_ALL_umol.rad"
    if mode == HPS_MODE_LABEL:
        return base_root / "runtime_state" / "emitters_hps_ALL_umol.rad"
    return base_root / "runtime_state" / "emitters_smd_ALL_umol.rad"


def _layout_file_for_mode(mode: str, workspace_root: Path | None = None) -> Path | None:
    base_root = workspace_root or ROOT
    mode = _normalize_mode(mode)
    if mode == MODE_COMPETITOR:
        return base_root / "runtime_state" / "spydr3_layout.json"
    if mode == HPS_MODE_LABEL:
        return base_root / "runtime_state" / "hps_layout.json"
    return base_root / "runtime_state" / "smd_layout.json"


def _visualization_dependency_files() -> list[Path]:
    root = ENGINE_PACKAGE_ROOT / "visualization"
    return [
        root / "visualize_ppfd.py",
        root / "heatmaps.py",
        root / "overlays.py",
        root / "data.py",
        root / "style.py",
    ]


def _live_workspace_sync_shell(req: RadianceRunRequest, workspace_root: Path, *, include_visuals: bool) -> str:
    commands: list[str] = [
        f"mkdir -p {shlex.quote(str(workspace_root / 'ies_sources'))}",
        f"mkdir -p {shlex.quote(str(workspace_root / 'runtime_state'))}",
        f"mkdir -p {shlex.quote(str(workspace_root / 'artifacts'))}",
    ]

    copy_pairs: list[tuple[Path, Path]] = [
        (ROOT / "ppfd_map.txt", workspace_root / "ppfd_map.txt"),
        (ROOT / "room.rad", workspace_root / "room.rad"),
        (ROOT / "sensor_points.txt", workspace_root / "sensor_points.txt"),
    ]

    layout_src = _layout_file_for_mode(req.mode)
    layout_dst = _layout_file_for_mode(req.mode, workspace_root)
    if layout_src and layout_dst:
        copy_pairs.append((layout_src, layout_dst))

    emitters_src = _emitters_file_for_mode(req.mode)
    emitters_dst = _emitters_file_for_mode(req.mode, workspace_root)
    if emitters_src and emitters_dst:
        copy_pairs.append((emitters_src, emitters_dst))

    mode = _normalize_mode(req.mode)
    if mode == MODE_COMPETITOR:
        copy_pairs.append((ROOT / "runtime_state" / "spydr3_power.txt", workspace_root / "runtime_state" / "spydr3_power.txt"))
        copy_pairs.append((ROOT / "runtime_state" / "spydr3_summary.txt", workspace_root / "runtime_state" / "spydr3_summary.txt"))
    elif mode == HPS_MODE_LABEL:
        copy_pairs.append((ROOT / "runtime_state" / "hps_power.txt", workspace_root / "runtime_state" / "hps_power.txt"))
        copy_pairs.append((ROOT / "runtime_state" / "hps_summary.txt", workspace_root / "runtime_state" / "hps_summary.txt"))
    else:
        copy_pairs.append((ROOT / "runtime_state" / "smd_summary.txt", workspace_root / "runtime_state" / "smd_summary.txt"))
        copy_pairs.append((ROOT / "ring_powers_optimized.json", workspace_root / "ring_powers_optimized.json"))

    for filename in PLANT_ARTIFACT_FILENAMES:
        copy_pairs.append(
            (
                ROOT / "runtime_state" / filename,
                workspace_root / "runtime_state" / filename,
            )
        )

    for src, dst in copy_pairs:
        commands.append(
            f"if [ -f {shlex.quote(str(src))} ]; then cp -f {shlex.quote(str(src))} {shlex.quote(str(dst))}; fi"
        )

    if include_visuals:
        src_outdir = _resolve_output_dir_path(req.mode, ROOT)
        dst_outdir = workspace_root / _output_dir_for_mode(req.mode)
        commands.append(f"mkdir -p {shlex.quote(str(dst_outdir))}")
        # Scatter HTML is generated on demand per-workspace, so copying it
        # from the shared root can propagate stale plots into unrelated runs.
        # Sync the PPFD CSV with the images so the latest run workspace keeps
        # the downloadable grid artifact alongside the rendered visuals.
        for name in ("ppfd_heatmap_annotated.png", "ppfd_heatmap_overlay.png", "ppfd_map.csv"):
            commands.append(
                f"if [ -f {shlex.quote(str(src_outdir / name))} ]; then cp -f {shlex.quote(str(src_outdir / name))} {shlex.quote(str(dst_outdir / name))}; fi"
            )
        commands.append(f"rm -f {shlex.quote(str(dst_outdir / 'ppfd_scatter_3d.html'))}")

    return " && ".join(commands)


def _visualize_shell(
    req: RadianceRunRequest,
    *,
    base_root: Path | None = None,
    skip_csv: bool = False,
    skip_secondary_plots: bool = False,
    skip_scatter: bool = False,
    scatter_only: bool = False,
) -> tuple[str, str]:
    overlay = _overlay_for_mode(req.mode, req.overlay)
    outdir = str(_resolve_output_dir_path(req.mode, base_root or ROOT))
    shell_cmd = (
        f'"$PY" -m rad_rebuild.radiance.engine.visualization.visualize_ppfd '
        f'--overlay {shlex.quote(overlay)} --outdir {shlex.quote(outdir)}'
    )
    if skip_csv:
        shell_cmd += " --skip-csv"
    if skip_secondary_plots:
        shell_cmd += " --skip-secondary-plots"
    if skip_scatter:
        shell_cmd += " --skip-scatter"
    if scatter_only:
        shell_cmd += " --scatter-only"
    return shell_cmd, outdir


def _workspace_artifact_env(env: dict[str, str], workspace_root: Path | None = None) -> dict[str, str]:
    if workspace_root is None or workspace_root == ROOT:
        return env
    env["RADIANCE_OUTPUT_ROOT"] = str(workspace_root)
    env["RADIANCE_RUNTIME_STATE_ROOT"] = str(workspace_root / "runtime_state")
    env["RADIANCE_VISUALIZATION_OUTPUT_ROOT"] = str(workspace_root)
    return env


def _visualize_command(req: RadianceRunRequest, env: dict[str, str], base_root: Path | None = None) -> tuple[list[str], str]:
    env = _workspace_artifact_env(env, base_root)
    _apply_visualize_env(env)
    shell_cmd, outdir = _visualize_shell(
        req,
        base_root=base_root,
        skip_secondary_plots=True,
        skip_scatter=True,
    )
    cmd = _local_command(shell_cmd)
    return cmd, outdir


def _scatter_command(req: RadianceRunRequest, env: dict[str, str], base_root: Path | None = None) -> tuple[list[str], str]:
    env = _workspace_artifact_env(env, base_root)
    _apply_visualize_env(env)
    shell_cmd, outdir = _visualize_shell(req, base_root=base_root, scatter_only=True)
    cmd = _local_command(shell_cmd)
    return cmd, outdir


def _ensure_visuals(req: RadianceRunRequest, env: dict[str, str], workspace_root: Path | None = None) -> Path:
    work_root = workspace_root or ROOT
    cmd, outdir = _visualize_command(req, env, work_root)
    outdir_path = _resolve_output_dir_path(req.mode, work_root)
    annotated = outdir_path / "ppfd_heatmap_annotated.png"
    overlay = outdir_path / "ppfd_heatmap_overlay.png"
    visualizer_paths = _visualization_dependency_files()
    ppfd_map = work_root / "ppfd_map.txt"
    layout_path = _layout_file_for_mode(req.mode, work_root)
    emitters_path = _emitters_file_for_mode(req.mode, work_root)
    needs_render = not annotated.exists() or not overlay.exists()
    if ppfd_map.exists() and not needs_render:
        try:
            ppfd_mtime = ppfd_map.stat().st_mtime
            vis_mtime = min(annotated.stat().st_mtime, overlay.stat().st_mtime)
            if ppfd_mtime > vis_mtime:
                needs_render = True
        except Exception:
            needs_render = True
    if not needs_render:
        try:
            vis_mtime = min(annotated.stat().st_mtime, overlay.stat().st_mtime)
            for visualizer_path in visualizer_paths:
                if (
                    visualizer_path.exists()
                    and visualizer_path.stat().st_mtime > vis_mtime
                ):
                    needs_render = True
                    break
            for optional_path in (layout_path, emitters_path):
                if (
                    optional_path
                    and optional_path.exists()
                    and optional_path.stat().st_mtime > vis_mtime
                ):
                    needs_render = True
                    break
        except Exception:
            needs_render = True
    if needs_render:
        _run_cmd(cmd, cwd=work_root, env=env)
    return outdir_path


def _ensure_scatter(req: RadianceRunRequest, env: dict[str, str], workspace_root: Path | None = None) -> Path:
    work_root = workspace_root or ROOT
    cmd, outdir = _scatter_command(req, env, work_root)
    outdir_path = _resolve_output_dir_path(req.mode, work_root)
    scatter_path = outdir_path / "ppfd_scatter_3d.html"
    visualizer_paths = _visualization_dependency_files()
    ppfd_map = work_root / "ppfd_map.txt"
    layout_path = _layout_file_for_mode(req.mode, work_root)
    emitters_path = _emitters_file_for_mode(req.mode, work_root)
    needs_render = not scatter_path.exists()
    if ppfd_map.exists() and not needs_render:
        try:
            if ppfd_map.stat().st_mtime > scatter_path.stat().st_mtime:
                needs_render = True
        except Exception:
            needs_render = True
    if not needs_render:
        try:
            scatter_mtime = scatter_path.stat().st_mtime
            for visualizer_path in visualizer_paths:
                if (
                    visualizer_path.exists()
                    and visualizer_path.stat().st_mtime > scatter_mtime
                ):
                    needs_render = True
                    break
            for optional_path in (layout_path, emitters_path):
                if (
                    optional_path
                    and optional_path.exists()
                    and optional_path.stat().st_mtime > scatter_mtime
                ):
                    needs_render = True
                    break
        except Exception:
            needs_render = True
    if needs_render:
        _run_cmd(cmd, cwd=work_root, env=env)
    return scatter_path


def _build_manifest(req: RadianceRunRequest, env: dict[str, str], outdir: Path, workspace_root: Path | None = None) -> dict[str, object]:
    work_root = workspace_root or ROOT
    geometry_paths = [
        work_root / "room.rad",
        work_root / "sensor_points.txt",
    ]
    layout_path = _layout_file_for_mode(req.mode, work_root)
    if layout_path:
        geometry_paths.append(layout_path)
    materials_paths = []
    emitters_path = _emitters_file_for_mode(req.mode, work_root)
    if emitters_path:
        materials_paths.append(emitters_path)
    generator_path = _generator_file_for_mode(req.mode)

    ppfd_txt = work_root / "ppfd_map.txt"
    ppfd_csv = _write_ppfd_csv(ppfd_txt, outdir)
    basis_fields = (
        basis_backend_request_fields(req.basis_backend, sim_mode=req.sim_mode, env=env)
        if _normalize_mode(req.mode) == MODE_SMD
        else {}
    )

    manifest = {
        "schema_version": RADIANCE_MANIFEST_SCHEMA_VERSION,
        "mode": req.mode,
        "visualization_directory": _output_dir_for_mode(req.mode),
        "geometry": _collect_files(geometry_paths),
        "materials": _collect_files(materials_paths),
        "renderer_params": {
            "sim_mode": req.sim_mode,
            "subpatch_grid": req.subpatch_grid,
            "mount_z_m": req.mount_z_m,
            "overlay": _overlay_for_mode(req.mode, req.overlay),
            "run_basis": req.run_basis,
            "w_min": req.w_min,
            "w_max": req.w_max,
            "smd_base_ring": req.smd_base_ring,
            "basis_backend": basis_fields.get("basis_backend"),
            "basis_backend_config": basis_fields.get("basis_backend_config"),
            "competitor_layout": req.competitor_layout,
            "hps_coverage_ft": req.hps_coverage_ft,
            "hps_z_m": req.hps_z_m,
            "hps_fixture_ppf": req.hps_fixture_ppf,
            "hps_input_watts": req.hps_input_watts,
            "hps_ies_variant": req.hps_ies_variant,
            "smd_model": "legacy" if (_normalize_mode(req.mode) == MODE_SMD and req.match_system_ppe) else ("curve" if _normalize_mode(req.mode) == MODE_SMD else None),
            "smd_curve_model": SMD_CURVE_MODEL_VERSION if _normalize_mode(req.mode) == MODE_SMD else None,
        },
        "grid": {
            "ppfd_map_txt": str(ppfd_txt.relative_to(work_root)) if ppfd_txt.exists() else None,
            "ppfd_map_csv": str(ppfd_csv.relative_to(work_root)) if ppfd_csv.exists() else None,
        },
        "outputs": {
            "ppfd_csv": _file_entry(ppfd_csv) if ppfd_csv.exists() else None,
            "annotated_heatmap": _file_entry(outdir / "ppfd_heatmap_annotated.png")
            if (outdir / "ppfd_heatmap_annotated.png").exists()
            else None,
        },
        "renderer_inputs": {
            "length_ft": req.length_ft,
            "width_ft": req.width_ft,
            "target_ppfd": req.target_ppfd,
            "peak_capping_enabled": req.peak_capping_enabled,
            "competitor_layout": req.competitor_layout,
        },
        "scale_factor": {
            "eff_scale": float(env.get("EFF_SCALE", "1.0")),
            "match_system_ppe": False if _normalize_mode(req.mode) == HPS_MODE_LABEL else req.match_system_ppe,
            "peak_capping_enabled": req.peak_capping_enabled,
        },
        "spd_hash": _sha256_file(generator_path) if generator_path.exists() else None,
        "generator": _portable_path(generator_path) if generator_path.exists() else None,
    }
    if _normalize_mode(req.mode) == HPS_MODE_LABEL:
        manifest["hps_profile"] = hps_profile_manifest_dict()
        manifest["calibration_sensitive_elements"] = hps_calibration_sensitive_elements()
    return manifest


def _manifest_dependencies(req: RadianceRunRequest, outdir: Path, workspace_root: Path | None = None) -> list[Path | None]:
    work_root = workspace_root or ROOT
    deps = [
        BACKEND_SERVER_FILE,
        ENGINE_PACKAGE_ROOT / "visualization" / "visualize_ppfd.py",
        work_root / "ppfd_map.txt",
        work_root / "room.rad",
        work_root / "sensor_points.txt",
        _layout_file_for_mode(req.mode, work_root),
        _emitters_file_for_mode(req.mode, work_root),
        _generator_file_for_mode(req.mode),
        outdir / "ppfd_heatmap_annotated.png",
        outdir / "ppfd_heatmap_overlay.png",
    ]
    if _normalize_mode(req.mode) == MODE_SMD:
        deps.extend(
            [
                ENGINE_PACKAGE_ROOT / "photometry" / "smd_curve_model.py",
                get_settings().smd_white_vf_csv,
                get_settings().smd_white_ppe_csv,
                get_settings().smd_red_vf_csv,
                get_settings().smd_red_ppe_csv,
            ]
        )
    return deps


def _manifest_matches_current_layout(cached: dict[str, object], req: RadianceRunRequest) -> bool:
    expected_dir = _output_dir_for_mode(req.mode)
    schema_version = cached.get("schema_version", 0)
    if not isinstance(schema_version, (str, int, float)):
        schema_version = 0
    if int(schema_version or 0) != RADIANCE_MANIFEST_SCHEMA_VERSION:
        return False
    if str(cached.get("visualization_directory", "") or "") != expected_dir:
        return False
    grid = cached.get("grid")
    if isinstance(grid, dict):
        csv_rel = str(grid.get("ppfd_map_csv", "") or "")
        if csv_rel and not csv_rel.startswith(f"{expected_dir}/"):
            return False
    outputs = cached.get("outputs")
    if isinstance(outputs, dict):
        for key in ("ppfd_csv", "annotated_heatmap"):
            entry = outputs.get(key)
            if isinstance(entry, dict):
                path_value = str(entry.get("path", "") or "")
                if path_value and f"/{expected_dir}/" not in path_value:
                    return False
    return True


def _get_or_build_manifest(req: RadianceRunRequest, env: dict[str, str], outdir: Path, workspace_root: Path | None = None) -> tuple[dict[str, object], Path]:
    manifest_path = _manifest_path(req.mode, workspace_root)
    work_root = workspace_root or ROOT
    ppfd_txt = work_root / "ppfd_map.txt"
    if ppfd_txt.exists():
        _write_ppfd_csv(ppfd_txt, outdir)
    deps = _manifest_dependencies(req, outdir, workspace_root)
    if _cache_fresh(manifest_path, deps):
        try:
            cached = json.loads(manifest_path.read_text())
            if isinstance(cached, dict) and _manifest_matches_current_layout(cached, req):
                return cached, manifest_path
        except Exception:
            pass
    manifest = _build_manifest(req, env, outdir, workspace_root)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest, manifest_path


def _normalize_visualization_dir_refs(value: object, mode: str) -> object:
    expected_dir = _output_dir_for_mode(mode)
    legacy_dirs = [
        "ppfd_visualizations_spydr3",
        "ppfd_visualizations",
    ]
    if isinstance(value, str):
        normalized = value
        for legacy in legacy_dirs:
            normalized = normalized.replace(legacy, expected_dir)
        return normalized
    if isinstance(value, list):
        return [_normalize_visualization_dir_refs(item, mode) for item in value]
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for key, item in value.items():
            out[key] = _normalize_visualization_dir_refs(item, mode)
        if "visualization_directory" in out:
            out["visualization_directory"] = expected_dir
        return out
    return value
