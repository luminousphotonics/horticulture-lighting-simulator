import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import * as THREE from "three";

import {
  PNG_HEIGHT,
  PNG_WIDTH,
  PRESENTATION_EXPORTER_ID,
  SVG_HEIGHT,
  SVG_WIDTH,
  buildPresentationSvg,
  colorForPpfd,
  createSvgBlob,
  displayHeight,
  downloadBlob,
  rasterizeSvgBlobToPng,
} from "../src/fspm_optics/resources/scatter/presentation-export.js";

const root = new URL("../src/fspm_optics/resources/scatter/", import.meta.url);
const html = readFileSync(new URL("index.html", root), "utf8");
const javascript = readFileSync(new URL("main.js", root), "utf8");
const css = readFileSync(new URL("styles.css", root), "utf8");
const exporter = readFileSync(new URL("presentation-export.js", root), "utf8");
const mainApplication = readFileSync(
  new URL("../src/fspm_optics/resources/web/result-view.js", import.meta.url),
  "utf8",
);
const mainHtml = readFileSync(
  new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
  "utf8",
);

test("main application action is the scatter lazy-load boundary", () => {
  const handler = javascript.indexOf("async function loadScatter()");
  const binaryFetch = javascript.indexOf("${artifactRoot}/${metadata.scatter.filename}", handler);
  const hashCheck = javascript.indexOf("await verifyHash", binaryFetch);
  const viewer = javascript.indexOf("createViewer", hashCheck);
  const activationStart = mainApplication.indexOf("function activateArtifactTile(");
  const validationStart = mainApplication.indexOf("function validateArtifactUrl(");
  const activation = mainApplication.slice(activationStart, validationStart);
  const disableBeforeValidation = activation.indexOf("disableArtifactTile(tile)");
  const strictValidation = activation.indexOf("validateArtifactUrl(candidate, expectedPath)");
  const rejectedReturn = activation.indexOf("if (!artifactUrl) return");
  const hrefActivation = activation.indexOf("tile.href = artifactUrl");

  assert.ok(handler > 0 && binaryFetch > handler && hashCheck > binaryFetch && viewer > hashCheck);
  assert.match(
    mainHtml,
    /<a class="artifact-card" id="scatter-card"[^>]*target="_blank"[^>]*rel="noopener"[^>]*aria-disabled="true"[^>]*tabindex="-1">/,
  );
  assert.match(
    mainApplication,
    /artifactPathForResult\(result, "scatter"\)/,
  );
  assert.match(
    mainApplication,
    /if \(kind === "scatter"\) return `\/runs\/\$\{runId\}\/scatter\/index\.html`/,
  );
  assert.match(
    mainApplication,
    /if \(kind === "scatter"\) return `\$\{prefix\}\/scatter\/index\.html`/,
  );
  assert.ok(activationStart > 0 && validationStart > activationStart);
  assert.ok(disableBeforeValidation >= 0 && strictValidation > disableBeforeValidation);
  assert.ok(rejectedReturn >= 0 && hrefActivation > rejectedReturn);
  assert.match(mainApplication, /tile\.removeAttribute\("href"\)/);
  assert.match(mainApplication, /tile\.setAttribute\("aria-disabled", "true"\)/);
  assert.match(mainApplication, /tile\.setAttribute\("tabindex", "-1"\)/);
  assert.match(mainApplication, /tile\.href = artifactUrl/);
  assert.match(mainApplication, /tile\.setAttribute\("aria-disabled", "false"\)/);
  assert.match(mainApplication, /tile\.removeAttribute\("tabindex"\)/);
  assert.match(mainApplication, /RUN_ID_PATTERN\.test\(activeRun \|\| ""\)/);
  assert.match(mainApplication, /typeof candidate !== "string" \|\| candidate\.length === 0/);
  assert.match(mainApplication, /artifactUrl\.protocol !== "http:"/);
  assert.match(mainApplication, /artifactUrl\.protocol !== "https:"/);
  assert.match(mainApplication, /artifactUrl\.pathname !== expectedPath/);
  assert.match(mainApplication, /artifactUrl\.origin !== window\.location\.origin/);
  assert.match(mainApplication, /artifactUrl\.search !== ""/);
  assert.match(mainApplication, /artifactUrl\.hash !== ""/);
  assert.match(mainApplication, /artifactUrl\.username !== ""/);
  assert.match(mainApplication, /artifactUrl\.password !== ""/);
  assert.match(
    mainApplication,
    /candidate !== expectedPath\s*&& candidate !== `\$\{window\.location\.origin\}\$\{expectedPath\}`/,
  );
  assert.doesNotMatch(mainApplication, /scatterLink/);
  assert.doesNotMatch(mainApplication, /fetch\(result\.ppfd_scatter_data_url/);
  assert.doesNotMatch(mainApplication, /artifacts\/\$\{metadata\.scatter\.filename\}/);
  assert.doesNotMatch(mainApplication, /three\.module\.js|OrbitControls|\bTHREE\b/);
  assert.match(javascript, /status\.textContent = "Metadata validated · loading scatter";\s*await loadScatter\(\)/);
});

test("scatter resolves live and precomputed artifacts without fallback", () => {
  assert.match(javascript, /\^\\\/precomputed\\\/\(\[0-9a-f\]\{32\}\)/);
  assert.match(
    javascript,
    /artifactRoot: `\/api\/precomputed\/playbacks\/\$\{playback\[1\]\}\/artifacts`/,
  );
  assert.match(
    javascript,
    /artifactRoot: `\/api\/runs\/\$\{live\[1\]\}\/artifacts`/,
  );
  assert.match(javascript, /fetch\(`\$\{artifactRoot\}\/visualization\.json`/);
  assert.match(
    javascript,
    /fetch\(`\$\{artifactRoot\}\/\$\{metadata\.scatter\.filename\}`/,
  );
});

test("successful scatter load removes the blocking gate", () => {
  const viewer = javascript.indexOf("current = createViewer");
  const hideGate = javascript.indexOf("emptyState.hidden = true", viewer);

  assert.ok(viewer > 0 && hideGate > viewer);
  assert.match(css, /#empty-state\[hidden\][^{]*\{[^}]*display: none !important;[^}]*pointer-events: none !important;/);
  assert.doesNotMatch(html, /The compact field remains unloaded until requested/);
  assert.match(html, /id="reload-scatter"/);
  assert.match(html, /id="retry-scatter"[^>]*hidden/);
});

test("scatter viewer declares axes controls disposal and event-driven rendering", () => {
  for (const label of ["X · position (m)", "Y · position (m)", "Z · PPFD (µmol/m²/s)"]) {
    assert.ok(html.includes(label));
  }
  assert.match(html, /Drag to orbit\. Right-drag to pan\. Scroll or pinch to zoom\./);
  assert.match(javascript, /controls\.addEventListener\("change", render\)/);
  assert.match(javascript, /controls\.enablePan = true/);
  assert.match(javascript, /controls\.enableZoom = true/);
  assert.match(javascript, /window\.addEventListener\("resize", resize\)/);
  assert.match(javascript, /positions\[index \* 3 \+ 2\] = displayHeight\(points\.ppfd\[index\], vertical\)/);
  assert.doesNotMatch(javascript, /cloud\.scale\.z|cloud\.position\.z/);
  assert.match(javascript, /const sceneFloor = vertical\.scene_floor_height/);
  assert.match(javascript, /grid\.position\.z = sceneFloor/);
  assert.match(javascript, /axes\.position\.set\(xMin, yMin, sceneFloor\)/);
  assert.match(javascript, /const worldSpan = Math\.max\(horizontalSpan, frameVerticalSpan\)/);
  assert.match(javascript, /const center = new THREE\.Vector3\([^;]*frameLower[^;]*frameUpper/);
  assert.match(javascript, /geometry\.dispose\(\)/);
  assert.match(javascript, /material\.dispose\(\)/);
  assert.match(javascript, /renderer\.dispose\(\)/);
  assert.doesNotMatch(javascript, /requestAnimationFrame/);
});

test("visible scatter legend uses declared metadata limits and units", () => {
  assert.match(html, /id="legend-maximum"/);
  assert.match(html, /id="legend-center"/);
  assert.match(html, /id="legend-minimum"/);
  assert.match(html, /µmol\/m²\/s · target-centered display colors/);
  assert.match(javascript, /legendMinimum\.textContent = format\(limits\[0\]\)/);
  assert.match(javascript, /legendCenter\.textContent = format\(value\.display\.requested_lighting_target_ppfd_umol_m2_s\)/);
  assert.match(javascript, /legendMaximum\.textContent = format\(limits\[1\]\)/);
});

test("visible Z scale and sidebar report authoritative raw PPFD", () => {
  assert.match(html, /id="z-range-maximum"/);
  assert.match(html, /id="z-range-target"/);
  assert.match(html, /id="z-range-minimum"/);
  assert.match(html, /Raw PPFD remains authoritative/);
  assert.match(javascript, /Raw PPFD.*raw_minimum_ppfd_umol_m2_s.*raw_maximum_ppfd_umol_m2_s/);
  assert.match(javascript, /zRangeMinimum\.textContent = format\(vertical\.declared_vmin_ppfd_umol_m2_s\)/);
  assert.match(javascript, /zRangeTarget\.textContent = format\(vertical\.requested_lighting_target_ppfd_umol_m2_s\)/);
  assert.match(javascript, /zRangeMaximum\.textContent = format\(vertical\.declared_vmax_ppfd_umol_m2_s\)/);
  assert.match(javascript, /function displayHeight\(rawPpfd, vertical\)/);
  assert.match(javascript, /function rawPpfdFromDisplayHeight\(displayHeightValue, vertical\)/);
  assert.doesNotMatch(javascript, /Math\.min\([^)]*displayHeight|Math\.max\([^)]*displayHeight/);
});

test("scatter viewer uses only run-local vendored Three.js", () => {
  assert.match(html, /\.\/vendor\/three\.module\.js/);
  assert.match(html, /\.\/vendor\/addons\//);
  assert.doesNotMatch(html + javascript, /https?:\/\//);
  assert.doesNotMatch(html + javascript, /react|vue|angular/i);
});

test("presentation controls stay gated until the validated scatter is ready", () => {
  assert.match(html, /<h2>Presentation export<\/h2>/);
  assert.match(html, /id="export-title"[^>]*value="PPFD distribution"/);
  assert.match(html, /id="export-svg"[^>]*disabled/);
  assert.match(html, /id="export-png"[^>]*disabled/);
  assert.match(html, /id="export-status"[^>]*role="status"/);
  const loadStart = javascript.indexOf("async function loadScatter()");
  const hashCheck = javascript.indexOf("await verifyHash", loadStart);
  const viewerReady = javascript.indexOf("current = createViewer", hashCheck);
  const exportsReady = javascript.indexOf("setExportEnabled(true)", viewerReady);
  assert.ok(loadStart > 0 && hashCheck > loadStart && viewerReady > hashCheck && exportsReady > viewerReady);
  assert.match(javascript, /setExportStatus\(`Export failed: \$\{message\}`, "error"\)/);
  const exportHandler = javascript.slice(
    javascript.indexOf("async function exportPresentation"),
    javascript.indexOf("function setExportEnabled"),
  );
  assert.doesNotMatch(exportHandler, /unavailable\(/);
});

test("presentation SVG is a complete depth-aware vector composition", () => {
  const metadata = exportMetadata();
  const points = exportPoints();
  const camera = exportCamera();
  const title = 'Study <A> & "validated"';
  const svg = buildPresentationSvg({
    THREE,
    camera,
    target: new THREE.Vector3(0, 0, 1),
    points,
    metadata,
    title,
  });

  assert.match(svg, new RegExp(`viewBox="0 0 ${SVG_WIDTH} ${SVG_HEIGHT}"`));
  assert.equal((svg.match(/<circle data-sample-index=/g) ?? []).length, points.count);
  assert.doesNotMatch(svg, /<image\b/i);
  assert.ok(svg.indexOf('data-sample-index="2"') < svg.indexOf('data-sample-index="0"'));
  assert.match(svg, /id="projected-floor-grid"/);
  assert.equal((svg.match(/data-grid-line=/g) ?? []).length, 10);
  for (const axis of ["x", "y", "z"]) assert.match(svg, new RegExp(`data-axis="${axis}"`));
  for (const label of ["X position (m)", "Y position (m)", "Z PPFD (µmol·m⁻²·s⁻¹)"]) assert.ok(svg.includes(label));
  assert.ok(svg.includes("Validated raw samples · no interpolation"));
  assert.ok(svg.includes("Study &lt;A&gt; &amp; &quot;validated&quot;"));
  assert.match(svg, /id="ppfd-legend"/);
  assert.match(svg, /µmol·m⁻²·s⁻¹/);
  assert.match(svg, /#440154/);
  assert.match(svg, /#fde725/);
  assert.match(svg, /clip-path="url\(#plot-clip\)"/);
  assert.doesNotMatch(svg, /<script\b|<image\b|@font-face|\bhref=/i);
  assert.doesNotMatch(svg.replace("http://www.w3.org/2000/svg", ""), /https?:\/\//i);

  const encodedMetadata = svg.match(/<metadata[^>]*>(.*?)<\/metadata>/)?.[1];
  assert.ok(encodedMetadata);
  const embedded = JSON.parse(decodeXml(encodedMetadata));
  assert.equal(embedded.exporter.id, PRESENTATION_EXPORTER_ID);
  assert.equal(embedded.run_id, metadata.run_id);
  assert.equal(embedded.field_identity_sha256, metadata.field.identity_sha256);
  assert.equal(embedded.sample_count, points.count);
  assert.deepEqual(embedded.declared_ppfd_color_limits_umol_m2_s, [800, 1200]);
  assert.deepEqual(embedded.reference, {kind: "requested_lighting_target", ppfd_umol_m2_s: 1000});
  assert.equal(embedded.vertical_display_transform_policy_id, "requested_target_window_ppfd_to_room_span_v1");
  assert.deepEqual(embedded.camera.target, [0, 0, 1]);
  assert.deepEqual(embedded.camera.up, [0, 0, 1]);
  assert.equal(embedded.camera.fov_degrees, 42);
  assert.equal(embedded.camera.projection_aspect, 16 / 9);
  assert.match(embedded.scientific_authority, /Raw PPFD remains authoritative/);
  assert.match(embedded.export_transform_policy, /No interpolation, smoothing, correction, or scientific rescaling/);
});

test("presentation projection shares the declared transform and colormap without observed-range normalization", () => {
  const metadata = exportMetadata();
  const vertical = metadata.scatter.vertical_display_transform;
  assert.equal(displayHeight(700, vertical), -1);
  assert.equal(displayHeight(1300, vertical), 5);
  assert.deepEqual(colorForPpfd(700, [800, 1200], metadata.display.colormap.anchors_srgb_8bit), [68, 1, 84]);
  assert.deepEqual(colorForPpfd(1300, [800, 1200], metadata.display.colormap.anchors_srgb_8bit), [253, 231, 37]);
  assert.doesNotMatch(exporter, /observed_(minimum|maximum).*color|normalize.*observed/i);
  assert.doesNotMatch(exporter, /\b(griddata|resamplePoints|smoothPoints|interpolateSamples)\b/i);
  assert.match(exporter, /displayHeight\(ppfd, vertical\)/);
  assert.match(exporter, /projectedPoints\.sort\(\(left, right\) => right\.depth - left\.depth/);

  const first = buildPresentationSvg({
    THREE,
    camera: exportCamera(),
    target: new THREE.Vector3(0, 0, 1),
    points: exportPoints(),
    metadata,
    title: "Camera A",
  });
  const movedCamera = exportCamera();
  movedCamera.position.set(8, 0, 5);
  movedCamera.lookAt(0, 0, 1);
  movedCamera.updateMatrixWorld(true);
  const second = buildPresentationSvg({
    THREE,
    camera: movedCamera,
    target: new THREE.Vector3(0, 0, 1),
    points: exportPoints(),
    metadata,
    title: "Camera A",
  });
  assert.notDeepEqual(pointCoordinates(first), pointCoordinates(second));
});

test("achieved-mean references remain explicit in export metadata and legend", () => {
  const metadata = exportMetadata();
  metadata.display.reference = {
    kind: "achieved_final_baseline_mean",
    center_ppfd_umol_m2_s: 850,
  };
  metadata.display.reference_ppfd_umol_m2_s = 850;
  metadata.display.color_limits_ppfd_umol_m2_s = [650, 1050];
  metadata.scatter.vertical_display_transform.policy_id = "achieved_baseline_window_ppfd_to_room_span_v1";
  const svg = buildPresentationSvg({
    THREE,
    camera: exportCamera(),
    target: new THREE.Vector3(0, 0, 1),
    points: exportPoints(),
    metadata,
    title: "HPS field",
  });
  const encodedMetadata = svg.match(/<metadata[^>]*>(.*?)<\/metadata>/)?.[1];
  const embedded = JSON.parse(decodeXml(encodedMetadata));
  assert.deepEqual(embedded.reference, {
    kind: "achieved_final_baseline_mean",
    ppfd_umol_m2_s: 850,
  });
  assert.match(svg, /Reference · achieved mean · 850/);
});

test("4K PNG rasterization and downloads revoke every Blob URL", async () => {
  const revoked = [];
  const created = [];
  const UrlApi = {
    createObjectURL(blob) {
      const url = `blob:test-${created.length}`;
      created.push({url, blob});
      return url;
    },
    revokeObjectURL(url) { revoked.push(url); },
  };
  const raster = {draw: null, encoded: null};
  const canvas = {
    width: 0,
    height: 0,
    getContext() {
      return {drawImage: (...args) => { raster.draw = args; }};
    },
    toBlob(callback, type) {
      raster.encoded = {width: this.width, height: this.height, type};
      callback(new Blob(["png"], {type}));
    },
  };
  class FakeImage {
    set src(value) {
      this._src = value;
      if (value) queueMicrotask(() => this.onload());
    }
    get src() { return this._src; }
  }
  const rasterDocument = {createElement: (name) => {
    assert.equal(name, "canvas");
    return canvas;
  }};
  const svgBlob = createSvgBlob("<svg/>");
  const pngBlob = await rasterizeSvgBlobToPng(svgBlob, {
    documentRef: rasterDocument,
    UrlApi,
    ImageCtor: FakeImage,
  });
  assert.equal(pngBlob.type, "image/png");
  assert.deepEqual(raster.encoded, {width: PNG_WIDTH, height: PNG_HEIGHT, type: "image/png"});
  assert.deepEqual(raster.draw.slice(1), [0, 0, PNG_WIDTH, PNG_HEIGHT]);
  assert.deepEqual(revoked, ["blob:test-0"]);

  const anchor = {
    hidden: false,
    clicked: false,
    click() { this.clicked = true; },
    remove() { this.removed = true; },
  };
  const downloadDocument = {
    body: {append(value) { assert.equal(value, anchor); }},
    createElement(name) { assert.equal(name, "a"); return anchor; },
  };
  await downloadBlob(pngBlob, "ppfd-scatter-test-4k.png", {
    documentRef: downloadDocument,
    UrlApi,
    schedule: (callback) => callback(),
  });
  assert.equal(anchor.download, "ppfd-scatter-test-4k.png");
  assert.equal(anchor.clicked, true);
  assert.equal(anchor.removed, true);
  assert.deepEqual(revoked, ["blob:test-0", "blob:test-1"]);
});

test("new presentation resource is published and remains explicitly allowlisted", () => {
  const visualization = readFileSync(
    new URL("../src/fspm_optics/application/visualization.py", import.meta.url),
    "utf8",
  );
  const proposed = readFileSync(
    new URL("../src/fspm_optics/application/proposed.py", import.meta.url),
    "utf8",
  );
  const workspaces = readFileSync(
    new URL("../src/fspm_optics/web/workspaces.py", import.meta.url),
    "utf8",
  );
  const packageConfig = readFileSync(
    new URL("../pyproject.toml", import.meta.url),
    "utf8",
  );
  assert.match(visualization, /_VIEWER_FILES = \([^\n]*"presentation-export\.js"/);
  assert.equal((proposed.match(/"ppfd-scatter-viewer\/presentation-export\.js"/g) ?? []).length, 2);
  assert.match(workspaces, /"presentation-export\.js": "ppfd-scatter-viewer\/presentation-export\.js"/);
  assert.match(workspaces, /mapped = SCATTER_VIEWER_FILES\.get\(relative\)/);
  assert.match(workspaces, /if mapped is None:\s*raise WorkspaceSafetyError/);
  assert.match(packageConfig, /"resources\/scatter\/\*\.js"/);
});

function exportCamera() {
  const camera = new THREE.PerspectiveCamera(42, 1, .01, 1000);
  camera.up.set(0, 0, 1);
  camera.position.set(0, -10, 5);
  camera.lookAt(0, 0, 1);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  return camera;
}

function exportPoints() {
  return {
    x: new Float32Array([0, 1, -1]),
    y: new Float32Array([-5, 0, 5]),
    ppfd: new Float32Array([700, 1000, 1300]),
    count: 3,
  };
}

function exportMetadata() {
  return {
    run_id: "a".repeat(32),
    field: {identity_sha256: "b".repeat(64), sample_count: 3},
    grid: {resolution: {x: 5, y: 3}},
    display: {
      color_limits_ppfd_umol_m2_s: [800, 1200],
      reference_ppfd_umol_m2_s: 1000,
      reference: {kind: "requested_lighting_target", center_ppfd_umol_m2_s: 1000},
      colormap: {
        anchors_srgb_8bit: [
          [68, 1, 84], [70, 50, 126], [54, 92, 141], [39, 127, 142],
          [31, 161, 135], [74, 193, 109], [160, 218, 57], [253, 231, 37],
        ],
      },
    },
    scatter: {
      vertical_display_transform: {
        policy_id: "requested_target_window_ppfd_to_room_span_v1",
        scale: .01,
        offset: -8,
        scene_floor_height: -1.5,
        horizontal_reference_span_m: 10,
        display_vertical_span: 7.2,
      },
    },
  };
}

function decodeXml(value) {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}

function pointCoordinates(svg) {
  return [...svg.matchAll(/<circle data-sample-index="(\d+)" cx="([^"]+)" cy="([^"]+)"/g)]
    .map((match) => match.slice(1).join(":"));
}
