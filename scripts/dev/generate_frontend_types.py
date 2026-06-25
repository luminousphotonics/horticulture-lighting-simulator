"""Generate the small frontend JSDoc API contract from OpenAPI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = ROOT / "docs" / "api" / "radiance-openapi.json"
OUTPUT_PATH = ROOT / "src" / "rad_rebuild" / "web" / "static" / "js" / "api-types.js"
SCHEMAS = [
    "PublicErrorResponse",
    "LayoutResponse",
    "RadianceImagesResponse",
    "RadianceManifestResponse",
    "RadianceMetricsResponse",
    "AssemblyRoomResponse",
    "AssemblyAssetsResponse",
    "AssemblyAxisMappingResponse",
    "AssemblyModuleAssetResponse",
    "AssemblyFixturePointResponse",
    "AssemblyAssetFallbackResponse",
    "AssemblyInstanceResponse",
    "AssemblySceneResponse",
    "AssemblyPhotometricBoundsResponse",
    "AssemblyPhotometricColormapResponse",
    "AssemblyPhotometricColorScaleResponse",
    "AssemblyPhotometricOrientationResponse",
    "AssemblyPhotometricLayerResponse",
    "RadianceRunResponse",
    "JobTailResponse",
    "RuntimeSetupCommandResponse",
    "RuntimePrecomputedModeStatusResponse",
    "RuntimeDockerModeStatusResponse",
    "RuntimeLocalModeStatusResponse",
    "RuntimeModesStatusResponse",
    "RuntimePrivatePhotometryStatusResponse",
    "RuntimeStatusResponse",
    "ElectricalEstimateStageResponse",
    "ElectricalEstimateResponse",
]
OVERRIDES = {
    "all_positions": "Array<[number, number]>",
    "module_counts": "Record<string, number>",
    "detail": "Record<string, unknown> | null",
    "RadianceManifestResponse.manifest": "Record<string, unknown>",
    "metrics": "Record<string, unknown>",
    "cost_estimate": "Record<string, unknown> | null",
    "room": "AssemblyRoomResponse",
    "assets": "AssemblyAssetsResponse",
    "axis_mapping": "AssemblyAxisMappingResponse",
    "module_assets": "Record<string, AssemblyModuleAssetResponse>",
    "points": "AssemblyFixturePointResponse[]",
    "asset_fallbacks_used": "AssemblyAssetFallbackResponse[]",
    "bounds_m": "AssemblyPhotometricBoundsResponse",
    "colormap": "AssemblyPhotometricColormapResponse",
    "color_scale": "AssemblyPhotometricColorScaleResponse",
    "orientation": "AssemblyPhotometricOrientationResponse",
    "warnings": "string[]",
    "missing_asset_keys": "string[]",
    "instance_ids": "string[]",
    "AssemblyAxisMappingResponse.cad_horizontal": 'Array<"x" | "z">',
    "AssemblyAxisMappingResponse.layout_horizontal": 'Array<"x" | "y">',
    "matrix": "number[]",
    "metadata": "Record<string, unknown>",
    "instances": "AssemblyInstanceResponse[]",
    "lines": "string[]",
    "supported_lighting_modes": "string[]",
    "setup_commands": "RuntimeSetupCommandResponse[]",
    "detected_executables": "Record<string, string>",
    "missing_executables": "string[]",
    "env_values": "Record<string, string | null>",
    "modes": "RuntimeModesStatusResponse",
    "RuntimeModesStatusResponse.precomputed": "RuntimePrecomputedModeStatusResponse",
    "RuntimeModesStatusResponse.live_docker": "RuntimeDockerModeStatusResponse",
    "RuntimeModesStatusResponse.live_local": "RuntimeLocalModeStatusResponse",
    "live_supported_modes": "string[]",
    "missing_env_vars": "string[]",
    "missing_files": 'Array<"SMD" | "Competitor" | "1000W HPS">',
    "supported_private_modes": 'Array<"SMD" | "Competitor" | "1000W HPS">',
    "stages": "ElectricalEstimateStageResponse[]",
    "notes": "string[]",
}


def schema_type(schema: dict[str, Any], name: str, owner: str | None = None) -> str:
    scoped_name = f"{owner}.{name}" if owner else name
    if scoped_name in OVERRIDES:
        return OVERRIDES[scoped_name]
    if name in OVERRIDES:
        return OVERRIDES[name]
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(item) for item in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(schema_type(part, name, owner) for part in schema["anyOf"])
    type_name = schema.get("type")
    if type_name == "integer":
        return "number"
    if type_name == "array":
        item = schema.get("items", {})
        return f"{schema_type(item, name, owner)}[]"
    if type_name == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Record<string, {schema_type(additional, name, owner)}>"
        return "Record<string, unknown>"
    if type_name == "null":
        return "null"
    if type_name in {"boolean", "number", "string"}:
        return type_name
    return "unknown"


def render_typedef(name: str, schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = [f" * @typedef {{object}} {name}"]
    for prop_name, prop_schema in sorted(properties.items()):
        optional = "" if prop_name in required else "="
        lines.append(f" * @property {{{schema_type(prop_schema, prop_name, name)}{optional}}} {prop_name}")
    return lines


def build() -> str:
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    schemas = openapi["components"]["schemas"]
    lines = [
        "// @ts-check",
        "",
        "/**",
        " * Frontend API contract typedefs generated from docs/api/radiance-openapi.json.",
        " * Run `npm run generate:api-types` after checked OpenAPI contract changes.",
        " *",
    ]
    for index, name in enumerate(SCHEMAS):
        if name not in schemas:
            continue
        if index:
            lines.append(" *")
        lines.extend(render_typedef(name, schemas[name]))
    lines.extend([" */", "", "export {};", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = build()
    if args.check:
        current = OUTPUT_PATH.read_text(encoding="utf-8")
        if current != content:
            print(f"{OUTPUT_PATH.relative_to(ROOT)} is out of date")
            return 1
        return 0
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
