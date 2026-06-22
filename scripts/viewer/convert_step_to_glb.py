#!/usr/bin/env python3
"""Convert a STEP assembly to a raw GLB for the proposed LED system viewer.

CadQuery and downstream mesh optimizers such as gltfpack are optional developer
tooling for preparing static viewer assets. They are intentionally not runtime
dependencies of the web app or core simulation package.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


DEFAULT_HIGH_RATIO = 0.45
DEFAULT_MEDIUM_RATIO = 0.18
DEFAULT_PROXY_RATIO = 0.04
LOD_LEVELS = ("high", "medium", "proxy")


@dataclass(frozen=True)
class LodGenerationOptions:
    viewer_dir: Path
    asset_name: str
    high_ratio: float = DEFAULT_HIGH_RATIO
    medium_ratio: float = DEFAULT_MEDIUM_RATIO
    proxy_ratio: float = DEFAULT_PROXY_RATIO
    gltfpack: str = "gltfpack"
    compress_meshopt: bool = False


def _load_cadquery() -> ModuleType:
    try:
        cq = importlib.import_module("cadquery")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "CadQuery is not installed. Install it in a separate developer tooling "
            "environment to convert STEP files; it is not required at app runtime."
        ) from exc
    return cq


def _load_assembly(cq: ModuleType, input_path: Path) -> Any:
    assembly_type = getattr(cq, "Assembly", None)
    if assembly_type is None:
        raise RuntimeError("CadQuery does not expose cq.Assembly; cannot load STEP assemblies.")

    loaders = []
    class_loader = getattr(assembly_type, "load", None)
    if callable(class_loader):
        loaders.append(class_loader)

    try:
        assembly = assembly_type()
    except TypeError:
        assembly = None
    if assembly is not None:
        instance_loader = getattr(assembly, "load", None)
        if callable(instance_loader):
            loaders.append(instance_loader)

    errors: list[str] = []
    for loader in loaders:
        try:
            loaded = loader(str(input_path))
        except Exception as exc:  # pragma: no cover - depends on optional CadQuery internals.
            errors.append(f"{loader!r}: {exc}")
            continue
        return loaded if loaded is not None else assembly

    if errors:
        raise RuntimeError("CadQuery could not load the STEP assembly:\n  " + "\n  ".join(errors))
    raise RuntimeError("CadQuery Assembly does not provide a supported load(path) API.")


def _save_glb(cq: ModuleType, assembly: Any, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    save = getattr(assembly, "save", None)
    if callable(save):
        attempts: tuple[dict[str, str], ...] = (
            {"exportType": "GLTF"},
            {"exportType": "glTF"},
            {},
        )
        errors: list[str] = []
        for kwargs in attempts:
            try:
                save(str(output_path), **kwargs)
            except TypeError as exc:
                errors.append(str(exc))
                continue
            except Exception as exc:  # pragma: no cover - depends on optional CadQuery internals.
                raise RuntimeError(f"CadQuery failed to write {output_path}: {exc}") from exc
            if output_path.is_file():
                return
        if errors:
            raise RuntimeError("CadQuery assembly.save did not accept supported GLB export options.")

    exporters = getattr(cq, "exporters", None)
    export = getattr(exporters, "export", None) if exporters is not None else None
    if callable(export):
        try:
            export(assembly, str(output_path))
        except Exception as exc:  # pragma: no cover - depends on optional CadQuery internals.
            raise RuntimeError(f"CadQuery exporters.export failed for {output_path}: {exc}") from exc
        if output_path.is_file():
            return

    raise RuntimeError("CadQuery did not produce a GLB file with a supported export API.")


def convert_step_to_glb(input_path: Path, output_path: Path) -> None:
    if not input_path.is_file():
        raise RuntimeError(f"STEP input file does not exist: {input_path}")
    cq = _load_cadquery()
    assembly = _load_assembly(cq, input_path)
    _save_glb(cq, assembly, output_path)


def infer_asset_name(output_path: Path) -> str:
    name = output_path.name
    if name.endswith(".raw.glb"):
        return name[: -len(".raw.glb")]
    if name.endswith(".glb"):
        return name[: -len(".glb")]
    return output_path.stem


def _ratio_arg(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a number") from exc
    if value <= 0 or value > 1:
        raise argparse.ArgumentTypeError("ratio must be greater than 0 and less than or equal to 1")
    return value


def _ratio_for_lod(options: LodGenerationOptions, lod_level: str) -> float:
    if lod_level == "high":
        return options.high_ratio
    if lod_level == "medium":
        return options.medium_ratio
    if lod_level == "proxy":
        return options.proxy_ratio
    raise ValueError(f"unsupported LOD level: {lod_level}")


def lod_output_path(options: LodGenerationOptions, lod_level: str) -> Path:
    return options.viewer_dir / f"{options.asset_name}.{lod_level}.glb"


def build_gltfpack_command(
    *,
    raw_glb: Path,
    output_glb: Path,
    ratio: float,
    gltfpack: str,
    compress_meshopt: bool,
) -> list[str]:
    command = [
        gltfpack,
        "-i",
        str(raw_glb),
        "-o",
        str(output_glb),
        "-si",
        f"{ratio:g}",
        "-kn",
        "-km",
        "-ke",
    ]
    if compress_meshopt:
        command.append("-cc")
    return command


def generate_lods(
    raw_glb: Path,
    options: LodGenerationOptions,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Path]:
    if not raw_glb.is_file():
        raise RuntimeError(f"raw GLB does not exist for LOD generation: {raw_glb}")
    options.viewer_dir.mkdir(parents=True, exist_ok=True)
    if options.compress_meshopt:
        print(
            "warning: --compress-meshopt enables gltfpack -cc; the browser viewer must configure "
            "GLTFLoader.setMeshoptDecoder(...) before loading these assets.",
            file=sys.stderr,
        )

    outputs: dict[str, Path] = {}
    for lod_level in LOD_LEVELS:
        output_glb = lod_output_path(options, lod_level)
        command = build_gltfpack_command(
            raw_glb=raw_glb,
            output_glb=output_glb,
            ratio=_ratio_for_lod(options, lod_level),
            gltfpack=options.gltfpack,
            compress_meshopt=options.compress_meshopt,
        )
        try:
            result = runner(command, check=False, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"gltfpack executable not found: {options.gltfpack}. Install gltfpack or pass --gltfpack."
            ) from exc
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            details = f": {output}" if output else ""
            raise RuntimeError(f"gltfpack failed while generating {lod_level} LOD{details}")
        outputs[lod_level] = output_glb
    return outputs


def convert_step_asset(
    input_path: Path,
    output_path: Path,
    lod_options: LodGenerationOptions | None = None,
) -> dict[str, Path]:
    convert_step_to_glb(input_path, output_path)
    outputs = {"raw": output_path}
    if lod_options is not None:
        outputs.update(generate_lods(output_path, lod_options))
    return outputs


def _load_batch_map(path: Path) -> list[tuple[str, Path]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"batch map does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"batch map is not valid JSON: {exc}") from exc

    jobs: list[tuple[str, Path]] = []
    if isinstance(payload, dict):
        for asset_name, input_step in sorted(payload.items()):
            if not isinstance(asset_name, str) or not isinstance(input_step, str):
                raise RuntimeError("batch map object must map asset names to STEP paths")
            jobs.append((asset_name, Path(input_step)))
        return jobs

    if isinstance(payload, list):
        for index, entry in enumerate(payload):
            if not isinstance(entry, dict):
                raise RuntimeError(f"batch map entry {index} must be an object")
            asset_name = entry.get("asset_name")
            input_step = entry.get("input_step")
            if not isinstance(asset_name, str) or not isinstance(input_step, str):
                raise RuntimeError(f"batch map entry {index} must include string asset_name and input_step fields")
            jobs.append((asset_name, Path(input_step)))
        return jobs

    raise RuntimeError("batch map must be a JSON object or array")


def _batch_lod_options(args: argparse.Namespace, asset_name: str) -> LodGenerationOptions:
    if args.viewer_dir is None:
        raise RuntimeError("--viewer-dir is required with --batch-map")
    return LodGenerationOptions(
        viewer_dir=args.viewer_dir,
        asset_name=asset_name,
        high_ratio=args.high_ratio,
        medium_ratio=args.medium_ratio,
        proxy_ratio=args.proxy_ratio,
        gltfpack=str(args.gltfpack),
        compress_meshopt=args.compress_meshopt,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert STEP assemblies to raw/debug GLBs and optional optimized viewer LODs."
    )
    parser.add_argument(
        "input_step",
        nargs="?",
        type=Path,
        help="Input STEP file. The STEP source is not stored in this repo.",
    )
    parser.add_argument("output_glb", nargs="?", type=Path, help="Output raw/debug GLB path to write.")
    parser.add_argument(
        "--make-lods",
        action="store_true",
        help="Generate optimized high/medium/proxy GLBs with gltfpack.",
    )
    parser.add_argument("--viewer-dir", type=Path, help="Viewer asset directory where optimized LOD GLBs are written.")
    parser.add_argument(
        "--asset-name",
        help="Base asset name for generated LOD files, for example fixture_centerpiece.",
    )
    parser.add_argument(
        "--high-ratio",
        type=_ratio_arg,
        default=DEFAULT_HIGH_RATIO,
        help="gltfpack simplification ratio for high LOD.",
    )
    parser.add_argument(
        "--medium-ratio",
        type=_ratio_arg,
        default=DEFAULT_MEDIUM_RATIO,
        help="gltfpack simplification ratio for medium LOD.",
    )
    parser.add_argument(
        "--proxy-ratio",
        type=_ratio_arg,
        default=DEFAULT_PROXY_RATIO,
        help="gltfpack simplification ratio for proxy LOD.",
    )
    parser.add_argument("--gltfpack", default="gltfpack", help="Path or executable name for gltfpack.")
    parser.add_argument(
        "--compress-meshopt",
        action="store_true",
        help="Pass gltfpack -cc. Requires the browser viewer to configure MeshoptDecoder.",
    )
    parser.add_argument(
        "--batch-map",
        type=Path,
        help="Optional JSON object mapping asset names to STEP paths, or an array of asset_name/input_step objects.",
    )
    parser.add_argument("--raw-dir", type=Path, help="Raw/debug GLB output directory for --batch-map.")
    return parser.parse_args(argv)


def _lod_options_from_args(args: argparse.Namespace, output_glb: Path) -> LodGenerationOptions | None:
    if not args.make_lods:
        return None
    viewer_dir = args.viewer_dir or output_glb.parent
    asset_name = args.asset_name or infer_asset_name(output_glb)
    return LodGenerationOptions(
        viewer_dir=viewer_dir,
        asset_name=asset_name,
        high_ratio=args.high_ratio,
        medium_ratio=args.medium_ratio,
        proxy_ratio=args.proxy_ratio,
        gltfpack=str(args.gltfpack),
        compress_meshopt=args.compress_meshopt,
    )


def _run_batch(args: argparse.Namespace) -> list[dict[str, Path]]:
    if args.batch_map is None:
        raise RuntimeError("--batch-map is required for batch mode")
    if args.raw_dir is None:
        raise RuntimeError("--raw-dir is required with --batch-map")
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Path]] = []
    for asset_name, input_step in _load_batch_map(args.batch_map):
        output_glb = args.raw_dir / f"{asset_name}.raw.glb"
        lod_options = _batch_lod_options(args, asset_name) if args.make_lods else None
        results.append(convert_step_asset(input_step, output_glb, lod_options))
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.batch_map is not None:
            batch_outputs = _run_batch(args)
            for output_group in batch_outputs:
                for path in output_group.values():
                    print(path)
            return 0
        if args.input_step is None or args.output_glb is None:
            raise RuntimeError("input_step and output_glb are required unless --batch-map is used")
        outputs = convert_step_asset(
            args.input_step,
            args.output_glb,
            _lod_options_from_args(args, args.output_glb),
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in outputs.values():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
