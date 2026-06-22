from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


HEATMAP_JS = Path("src/rad_rebuild/web/static/js/assembly-viewer/heatmap.js")


def test_assembly_viewer_heatmap_helpers() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for assembly viewer heatmap tests")

    script = f"""
import assert from "node:assert/strict";
import {{
  buildHeatmapTexturePixels,
  clampHeatmapOpacity,
  fetchPhotometricLayer,
  formatPpfdTooltipValue,
  lookupPpfdAtUv,
  mapPpfdToViridisRgba,
  normalizePpfdForViridis,
  photometricLayerUrlFromSceneUrl,
  validatePhotometricBinary,
  validatePhotometricMetadata,
  viridisRgbaAt,
}} from {HEATMAP_JS.resolve().as_uri()!r};

function metadata(overrides = {{}}) {{
  return {{
    schema_version: 1,
    mode: "SMD",
    units: "µmol/m²/s",
    grid_width: 3,
    grid_height: 3,
    value_count: 9,
    bounds_m: {{
      x_min: -1,
      x_max: 1,
      y_min: -1,
      y_max: 1,
      z_m: 0.005,
    }},
    min_ppfd: 10,
    max_ppfd: 90,
    mean_ppfd: 50,
    target_ppfd: 1000,
    encoding: "float32-le",
    colormap: {{
      name: "viridis",
      source: "matplotlib",
      normalization: "linear-clamped",
    }},
    color_scale: {{
      vmin: 25,
      vmax: 75,
      source: "simulation-visualization-mean-ppfd",
      clamp: true,
    }},
    orientation: {{
      plane: "xy",
      column_axis: "x",
      row_axis: "y",
      column_order: "ascending",
      row_order: "ascending",
      storage_order: "row-major",
      value_index: "row * grid_width + column",
    }},
    warnings: [],
    ...overrides,
  }};
}}

const sceneUrl = "/radiance-api/radiance/assembly-scene?session_id=abc%201&artifact_token=t%2B2&mode=SMD";
assert.equal(
  photometricLayerUrlFromSceneUrl(sceneUrl, "metadata"),
  "/radiance-api/radiance/assembly-photometric-layer?session_id=abc%201&artifact_token=t%2B2&mode=SMD",
);
assert.equal(
  photometricLayerUrlFromSceneUrl(sceneUrl, "binary"),
  "/radiance-api/radiance/assembly-photometric-layer.bin?session_id=abc%201&artifact_token=t%2B2&mode=SMD",
);
assert.throws(
  () => photometricLayerUrlFromSceneUrl("/radiance-api/radiance/metrics?session_id=abc", "metadata"),
  /assembly scene URL/,
);
assert.throws(
  () => photometricLayerUrlFromSceneUrl("/radiance-api/radiance/assembly-scene", "metadata"),
  /assembly scene URL/,
);

const validMetadata = metadata();
assert.equal(validatePhotometricMetadata(validMetadata), validMetadata);
assert.throws(
  () => validatePhotometricMetadata(metadata({{ colormap: {{ name: "turbo" }} }})),
  /viridis/,
);

const wrongLengthBuffer = new ArrayBuffer(8);
assert.throws(() => validatePhotometricBinary(wrongLengthBuffer, validMetadata), /36 bytes/);
const values = new Float32Array([10, 20, 30, 40, 50, 60, 70, 80, 90]);
assert.equal(validatePhotometricBinary(values.buffer, validMetadata).length, 9);

assert.deepEqual(viridisRgbaAt(0), [0x44, 0x01, 0x54, 255]);
assert.deepEqual(viridisRgbaAt(1), [0xfd, 0xe7, 0x25, 255]);
assert.deepEqual(mapPpfdToViridisRgba(25, validMetadata), [0x44, 0x01, 0x54, 255]);
assert.deepEqual(mapPpfdToViridisRgba(75, validMetadata), [0xfd, 0xe7, 0x25, 255]);

const pixels = buildHeatmapTexturePixels(values, validMetadata);
assert.equal(pixels.length, validMetadata.grid_width * validMetadata.grid_height * 4);
assert.deepEqual(Array.from(pixels.slice(0, 4)), [0x44, 0x01, 0x54, 255]);
assert.deepEqual(Array.from(pixels.slice(8 * 4, 8 * 4 + 4)), [0xfd, 0xe7, 0x25, 255]);

const rawMinMaxMetadata = metadata({{
  min_ppfd: 0,
  max_ppfd: 100,
  color_scale: {{
    vmin: 40,
    vmax: 60,
    source: "simulation-visualization-mean-ppfd",
    clamp: true,
  }},
}});
assert.equal(normalizePpfdForViridis(40, rawMinMaxMetadata.color_scale), 0);
assert.equal(normalizePpfdForViridis(60, rawMinMaxMetadata.color_scale), 1);
assert.deepEqual(mapPpfdToViridisRgba(0, rawMinMaxMetadata), [0x44, 0x01, 0x54, 255]);
assert.deepEqual(mapPpfdToViridisRgba(100, rawMinMaxMetadata), [0xfd, 0xe7, 0x25, 255]);
assert.equal(clampHeatmapOpacity(-1), 0.15);
assert.equal(clampHeatmapOpacity(0.55), 0.55);
assert.equal(clampHeatmapOpacity("0.8"), 0.8);
assert.equal(clampHeatmapOpacity(2), 1);
assert.equal(clampHeatmapOpacity(Number.NaN), 0.72);
assert.equal(formatPpfdTooltipValue(579.6), "580 µmol/m²/s");
assert.equal(formatPpfdTooltipValue(Number.NaN), "");
assert.equal(formatPpfdTooltipValue(Number.POSITIVE_INFINITY), "");

const lookupLayer = {{ metadata: validMetadata, values }};
assert.equal(lookupPpfdAtUv(lookupLayer, {{ x: 0, y: 0 }}), 10);
assert.equal(lookupPpfdAtUv(lookupLayer, {{ x: 0.5, y: 0.5 }}), 50);
assert.equal(lookupPpfdAtUv(lookupLayer, {{ x: 1, y: 1 }}), 90);
assert.equal(lookupPpfdAtUv(lookupLayer, {{ x: -2, y: 2 }}), 70);
assert.equal(lookupPpfdAtUv(lookupLayer, {{ x: 9, y: -9 }}), 30);

const fetchCalls = [];
const fetchedLayer = await fetchPhotometricLayer(sceneUrl, {{
  fetch: async (url, options) => {{
    fetchCalls.push([url, options?.cache]);
    if (url.includes(".bin")) {{
      return {{
        ok: true,
        arrayBuffer: async () => values.buffer.slice(0),
      }};
    }}
    return {{
      ok: true,
      json: async () => validMetadata,
    }};
  }},
}});
assert.deepEqual(fetchCalls, [
  ["/radiance-api/radiance/assembly-photometric-layer?session_id=abc%201&artifact_token=t%2B2&mode=SMD", "no-store"],
  ["/radiance-api/radiance/assembly-photometric-layer.bin?session_id=abc%201&artifact_token=t%2B2&mode=SMD", "no-store"],
]);
assert.equal(fetchedLayer.metadata, validMetadata);
assert.equal(fetchedLayer.values.length, values.length);
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_assembly_viewer_heatmap_helpers_do_not_reference_run_state() -> None:
    assert "lastCompletedRunKey" not in HEATMAP_JS.read_text(encoding="utf-8")
