import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  farRedRequestFields,
  MULTISPECTRAL_FSPM_SCOPE,
  synchronizeFarRedAnalysis,
} from "../src/fspm_optics/resources/web/analysis-scope-ui.js";
import {
  CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS,
  NATIVE_PROPOSED_SPECTRAL_BASIS,
  proposedSpectralBasisRequestFields,
  synchronizeProposedSpectralBasis,
} from "../src/fspm_optics/resources/web/spectral-basis-ui.js";
import {
  layoutModeRequestFields,
  PRACTICAL_LAYOUT_MODE,
  ROLLING_BENCH_LAYOUT_MODE,
  synchronizeLayoutMode,
} from "../src/fspm_optics/resources/web/layout-mode-ui.js";
import {
  BASIS_MATRIX_OPTIMIZED,
  proposedControlModeRequestFields,
  proposedControlModesAgree,
  synchronizeProposedControlMode,
  UNIFORM_MODULE_DIMMING,
} from "../src/fspm_optics/resources/web/proposed-control-mode-ui.js";
import {
  FULL_RING,
  proposedLayoutIdentitiesAgree,
  proposedRingModeRequestFields,
  REDUCED_ONE_RING,
  synchronizeProposedRingMode,
} from "../src/fspm_optics/resources/web/proposed-ring-mode-ui.js";
import {
  COB_SOURCE_SHAPE_SURROGATE,
  NATIVE_SMD,
  proposedSourceModeRequestFields,
  synchronizeProposedSourceMode,
} from "../src/fspm_optics/resources/web/proposed-source-mode-ui.js";
import {
  lightingTargetModeRequestFields,
  MEAN_TARGET,
  synchronizeLightingTargetMode,
  TARGET_CAPPED,
} from "../src/fspm_optics/resources/web/lighting-target-mode-ui.js";
import {
  SYSTEM_DISPLAY_LABELS,
  systemDisplayLabel,
} from "../src/fspm_optics/resources/viewer/system-labels.js";

function scopeElements({ checked = false } = {}) {
  const attributes = new Map();
  return {
    field: {
      hidden: false,
      setAttribute(name, value) { attributes.set(name, value); },
      attributes,
    },
    input: { checked, disabled: false },
  };
}

test("public entry point contains only the public form and shared result renderer", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/public-index.html", import.meta.url),
    "utf8",
  );
  const javascript = readFileSync(
    new URL("../src/fspm_optics/resources/web/public-app.js", import.meta.url),
    "utf8",
  );
  const form = html.slice(html.indexOf('<form id="run-form">'), html.indexOf("</form>"));
  assert.match(html, /\/static\/public-app\.js\?v=public-ui-v2/);
  assert.match(html, /\/static\/styles\.css\?v=public-ui-v2/);
  assert.match(html, />HORTICULTURE LIGHTING SIMULATOR</);
  assert.match(html, /href="https:\/\/luminousphotonics\.com\/"/);
  assert.match(html, /src="\/static\/homeleaf\.png"/);
  assert.match(html, /alt="Luminous Photonics home"/);
  assert.match(form, /id="run-button-label">Run Simulation</);
  assert.doesNotMatch(form, /Load Authenticated Result/);
  assert.doesNotMatch(html, /\/static\/app\.js/);
  assert.match(javascript, /from "\.\/result-view\.js"/);
  assert.doesNotMatch(javascript, /from "\.\/app\.js"/);
  assert.match(
    form,
    /id="lighting-target-mode-field" aria-hidden="true" hidden/,
  );
  assert.match(form, /id="lighting-target-field" hidden/);
  const controlIds = [
    "system", "layout-mode", "lighting-target-mode", "target-ppfd",
    "fspm-tolerance", "room-size", "aisle-mode", "run-button",
  ];
  for (const id of controlIds) assert.match(form, new RegExp(`id="${id}"`));
  assert.deepEqual(
    [...form.matchAll(/\bid="([^"]+)"/g)]
      .map((match) => match[1])
      .filter((id) => controlIds.includes(id)),
    controlIds,
  );
  assert.match(
    form,
    /FSPM Reference Tolerance <small>± µmol\/m²\/s<\/small>/,
  );
  assert.match(
    form,
    /<input id="fspm-tolerance" name="fspm_target_tolerance" type="number" min="0\.000001" step="any" value="75" required disabled>/,
  );
  for (const forbidden of [
    "execution-mode", "proposed-ring-mode", "disable-basis-matrix-solver",
    "cob-mode", "analysis-scope", "spectral-basis", "include-far-red",
    "mounting-height", "room-length", "room-width", "quality-fieldset",
    "fspm-target-mode", "fspm-target-ppfd",
  ]) assert.doesNotMatch(form, new RegExp(`id="${forbidden}"`));
  assert.doesNotMatch(form, /System default/);
  assert.doesNotMatch(form, /Rolling Bench uses a centered rigid 48 in fixture pitch/);
  assert.ok(form.indexOf("Rolling Bench") < form.indexOf("Practical Coverage"));
  assert.match(form, /10' × 10'/);
  assert.match(form, /15' × 30'/);
  assert.match(form, /30' × 50'/);
  assert.match(javascript, /capabilities\.lighting_target_modes/);
  assert.match(javascript, /capabilities\.fixture_layout_modes/);
  assert.match(javascript, /capabilities\.fspm_target_tolerance/);
  assert.match(javascript, /tolerance\.type !== "number"/);
  assert.match(javascript, /tolerance\.finite !== true/);
  assert.match(javascript, /tolerance\.strictly_positive !== true/);
  assert.match(
    javascript,
    /fspm_target_tolerance: Number\(toleranceInput\.value\)/,
  );
  assert.match(
    javascript,
    /toleranceInput\.value = String\(saved\.selector\.fspm_target_tolerance\)/,
  );
  assert.match(javascript, /toleranceInput\.disabled = true/);
  assert.match(
    javascript,
    /toleranceInput\.disabled = !capabilities\.fspm_target_tolerance\s+\.supported_systems\.includes\(system\)/,
  );
  assert.match(javascript, /preserveWhenUnsupported: true/);
  assert.match(javascript, /preserveWhenUnsupported && allowedValues\.length === 0/);
  assert.match(javascript, /payload\.live_simulation\?\.enabled !== false/);
});

test("public and live metrics use one shared public-release renderer", () => {
  const publicApp = readFileSync(
    new URL("../src/fspm_optics/resources/web/public-app.js", import.meta.url),
    "utf8",
  );
  const liveApp = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  const resultView = readFileSync(
    new URL("../src/fspm_optics/resources/web/result-view.js", import.meta.url),
    "utf8",
  );

  assert.match(publicApp, /createResultView\(\)/);
  assert.match(liveApp, /createResultView\(\)/);
  assert.doesNotMatch(publicApp + liveApp + resultView, /publicPresentation/);
  assert.match(resultView, /export function createResultView\(\)/);
  assert.match(resultView, /Matched with Conventional LED System's SPD/);
  assert.doesNotMatch(resultView, /Spectral control:/);
  assert.match(resultView, /setMetric\("modules", String\(metrics\.counts\.modules\)\)/);
});

test("public and live headers share a centered branded title structure", () => {
  const publicHtml = readFileSync(
    new URL("../src/fspm_optics/resources/web/public-index.html", import.meta.url),
    "utf8",
  );
  const liveHtml = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const css = readFileSync(
    new URL("../src/fspm_optics/resources/web/styles.css", import.meta.url),
    "utf8",
  );
  for (const html of [publicHtml, liveHtml]) {
    const header = html.slice(
      html.indexOf('<header class="masthead">'),
      html.indexOf("</header>"),
    );
    assert.match(header, /class="brand-home-link"[^>]+href="https:\/\/luminousphotonics\.com\/"/);
    assert.match(header, /class="brand-home-logo"[^>]+src="\/static\/homeleaf\.png"[^>]+alt="Luminous Photonics home"/);
    assert.match(header, /class="masthead-title"/);
    assert.match(header, />HORTICULTURE LIGHTING SIMULATOR</);
  }
  assert.doesNotMatch(
    publicHtml + liveHtml,
    /FSPM OPTICS \/ (?:AUTHENTICATED PLAYBACK|LOCAL NATIVE)/,
  );
  assert.match(
    css,
    /\.masthead \{[\s\S]*grid-template-columns: minmax\(84px, 1fr\) minmax\(0, auto\) minmax\(84px, 1fr\)/,
  );
  assert.match(css, /\.masthead-title \{[^}]*text-align: center/);
  assert.match(
    css,
    /\.brand-home-logo \{[^}]*width: clamp\(115px, 12vw, 180px\);[^}]*height: auto/,
  );
  assert.match(css, /grid-template-areas: "brand status" "title title"/);
});

test("system display labels are centralized while selector identities stay stable", () => {
  const liveHtml = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const publicHtml = readFileSync(
    new URL("../src/fspm_optics/resources/web/public-index.html", import.meta.url),
    "utf8",
  );
  const resultView = readFileSync(
    new URL("../src/fspm_optics/resources/web/result-view.js", import.meta.url),
    "utf8",
  );
  const viewer = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url),
    "utf8",
  );
  assert.deepEqual({...SYSTEM_DISPLAY_LABELS}, {
    proposed: "Modularized LED System",
    conventional: "Conventional LED System",
    hps: "1000W HPS System",
  });
  assert.equal(systemDisplayLabel("proposed"), "Modularized LED System");
  assert.equal(systemDisplayLabel("hps"), "1000W HPS System");
  for (const html of [liveHtml, publicHtml]) {
    assert.match(html, /option value="proposed">Modularized LED System/);
    assert.match(html, /option value="conventional">Conventional LED System/);
    assert.match(html, /option value="hps">1000W HPS System/);
    assert.doesNotMatch(html, /Proposed LED System|>HPS System</);
  }
  assert.match(resultView, /systemDisplayLabel\(metrics\.system_id\)/);
  assert.match(viewer, /systemDisplayLabel\(artifacts\.scene\.run\.system_id\)/);
  assert.match(liveHtml + publicHtml, /value="proposed"/);
  assert.match(liveHtml + publicHtml, /value="hps"/);
});

test("form control focus uses one thin border without an outer ring", () => {
  const css = readFileSync(
    new URL("../src/fspm_optics/resources/web/styles.css", import.meta.url),
    "utf8",
  );
  assert.match(css, /input:focus, input:focus-visible \{[^}]*border-color: var\(--color-focus\);[^}]*box-shadow: none;[^}]*outline: none;/s);
  assert.match(css, /select:focus, select:focus-visible \{[^}]*border-color: var\(--color-focus\);[^}]*box-shadow: none;[^}]*outline: none;/s);
  assert.doesNotMatch(css, /:where\([^)]*select[^)]*\):focus-visible/);
  assert.match(css, /select:not\(:disabled\):hover \{ border-color: var\(--color-accent\); \}/);
  assert.match(css, /select:not\(:disabled\):invalid, select:not\(:disabled\):user-invalid,[\s\S]*select:not\(:disabled\):invalid:focus, select:not\(:disabled\):user-invalid:focus \{ border-color: var\(--color-error\); \}/);
  assert.match(css, /input:disabled, select:disabled/);
});

test("website-derived dark tokens and accessibility rules cover all UI surfaces", () => {
  const webCss = readFileSync(
    new URL("../src/fspm_optics/resources/web/styles.css", import.meta.url),
    "utf8",
  );
  const viewerCss = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/styles.css", import.meta.url),
    "utf8",
  );
  const scatterCss = readFileSync(
    new URL("../src/fspm_optics/resources/scatter/styles.css", import.meta.url),
    "utf8",
  );
  for (const css of [webCss, viewerCss, scatterCss]) {
    assert.match(css, /color-scheme: dark/);
    assert.match(css, /--color-page: #0b0f14/);
    assert.match(css, /--color-surface: #141c27/);
    assert.match(css, /--color-border: #263244/);
    assert.match(css, /--color-accent: #d4af37/);
    assert.match(css, /--color-focus: #f0c84b/);
    assert.match(css, /prefers-reduced-motion: reduce/);
  }
  assert.match(webCss, /outline: var\(--focus-width\) solid var\(--color-focus\)/);
  assert.match(viewerCss, /outline: 3px solid var\(--color-focus\)/);
  assert.match(scatterCss, /outline: 3px solid var\(--color-focus\)/);
  assert.match(webCss, /@media \(max-width: 980px\)/);
  assert.match(webCss, /@media \(max-width: 620px\)/);
  assert.match(viewerCss, /@media \(max-width: 760px\)/);
  assert.match(scatterCss, /@media \(max-width: 780px\)/);

  const luminance = (hex) => {
    const channels = hex.match(/[0-9a-f]{2}/gi).map((value) => parseInt(value, 16) / 255);
    const linear = channels.map((value) => (
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    ));
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  };
  const contrast = (first, second) => {
    const [lighter, darker] = [luminance(first), luminance(second)]
      .sort((left, right) => right - left);
    return (lighter + 0.05) / (darker + 0.05);
  };
  assert.ok(contrast("#e8edf4", "#0b0f14") >= 4.5);
  assert.ok(contrast("#a7b2c3", "#141c27") >= 4.5);
  assert.ok(contrast("#0b0f14", "#d4af37") >= 4.5);
  assert.ok(contrast("#f0c84b", "#101826") >= 4.5);
});

test("lighting target policy defaults, labels, serialization, and HPS switch", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /id="lighting-target-mode"/);
  assert.match(html, /value="mean_target">Mean Target/);
  assert.match(html, /value="target_capped">Target-Capped/);
  assert.match(html, /id="fspm-tolerance"[^>]+value="75"/);

  const attributes = new Map();
  const field = {
    hidden: false,
    setAttribute(name, value) { attributes.set(name, value); },
  };
  const select = {value: TARGET_CAPPED, disabled: false};
  const targetLabel = {textContent: ""};
  const targetNote = {textContent: ""};
  assert.equal(synchronizeLightingTargetMode({
    system: "proposed", field, select, targetLabel, targetNote,
  }), true);
  assert.equal(targetLabel.textContent, "Maximum PPFD cap");
  assert.match(targetNote.textContent, /ceiling over authorized Stage A sensor-grid samples/);
  assert.deepEqual(
    lightingTargetModeRequestFields("proposed", TARGET_CAPPED),
    {lighting_target_mode: TARGET_CAPPED},
  );

  assert.equal(synchronizeLightingTargetMode({
    system: "hps", field, select, targetLabel, targetNote,
  }), false);
  assert.equal(field.hidden, true);
  assert.equal(attributes.get("aria-hidden"), "true");
  assert.equal(select.disabled, true);
  assert.equal(select.value, MEAN_TARGET);
  assert.deepEqual(
    lightingTargetModeRequestFields("hps", TARGET_CAPPED),
    {},
  );
});

test("COB Mode stays hidden and preserves the native SMD default", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /id="proposed-source-mode-field" aria-hidden="true" hidden/);
  assert.match(html, /<span>COB Mode<\/span>/);
  assert.match(html, /id="cob-mode" type="checkbox" disabled/);
  assert.doesNotMatch(html, /id="cob-mode"[^>]+checked/);

  const proposed = scopeElements();
  assert.equal(synchronizeProposedSourceMode({
    system: "proposed",
    ...proposed,
  }), true);
  assert.equal(proposed.field.hidden, true);
  assert.equal(proposed.field.attributes.get("aria-hidden"), "true");
  assert.equal(proposed.input.disabled, true);
  assert.equal(proposed.input.checked, false);
  assert.deepEqual(
    proposedSourceModeRequestFields("proposed", false),
    { proposed_source_mode: NATIVE_SMD },
  );
  assert.deepEqual(
    proposedSourceModeRequestFields("proposed", true),
    { proposed_source_mode: COB_SOURCE_SHAPE_SURROGATE },
  );

  const conventional = scopeElements({checked: true});
  assert.equal(synchronizeProposedSourceMode({
    system: "conventional",
    ...conventional,
  }), false);
  assert.equal(conventional.field.hidden, true);
  assert.equal(conventional.input.disabled, true);
  assert.equal(conventional.input.checked, false);
  assert.deepEqual(
    proposedSourceModeRequestFields("conventional", true),
    {},
  );
});

test("Aisle Mode is global, unchecked by default, and serialized for every system", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const app = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  assert.match(html, /<span>Aisle Mode<\/span>/);
  assert.match(html, /id="aisle-mode" name="aisle_mode" type="checkbox"/);
  assert.doesNotMatch(html, /id="aisle-mode"[^>]+checked/);
  assert.match(html, /fixed 2 ft perimeter aisle/);
  assert.doesNotMatch(app, /aisleModeInput\.checked\s*=/);
  assert.match(app, /aisle_mode:\s*aisleModeInput\.checked/);
});

test("far-red analysis is shown only for the multispectral scope", () => {
  const baseline = scopeElements({ checked: true });
  assert.equal(synchronizeFarRedAnalysis({
    scope: "baseline_ppfd",
    ...baseline,
  }), false);
  assert.equal(baseline.field.hidden, true);
  assert.equal(baseline.field.attributes.get("aria-hidden"), "true");
  assert.equal(baseline.input.disabled, true);
  assert.equal(baseline.input.checked, false);

  const multispectral = scopeElements();
  assert.equal(synchronizeFarRedAnalysis({
    scope: MULTISPECTRAL_FSPM_SCOPE,
    ...multispectral,
  }), true);
  assert.equal(multispectral.field.hidden, false);
  assert.equal(multispectral.field.attributes.get("aria-hidden"), "false");
  assert.equal(multispectral.input.disabled, false);
});

test("baseline request fields cannot retain a stale far-red selection", () => {
  assert.deepEqual(farRedRequestFields("baseline_ppfd", true), {});
  assert.deepEqual(
    farRedRequestFields(MULTISPECTRAL_FSPM_SCOPE, true),
    { include_far_red: true },
  );
  assert.deepEqual(
    farRedRequestFields(MULTISPECTRAL_FSPM_SCOPE, false),
    { include_far_red: false },
  );
});

test("scope UI synchronizes initial, restored, reset, navigation, and input state", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const app = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  assert.match(html, /id="far-red-analysis-field" aria-hidden="true" hidden/);
  assert.match(html, /id="include-far-red"[^>]+type="checkbox" disabled/);
  assert.match(app, /synchronizeAnalysisScopeState\(\);/);
  assert.match(app, /addEventListener\("pageshow"/);
  assert.match(app, /addEventListener\("popstate", synchronizeAnalysisScopeState\)/);
  assert.match(app, /addEventListener\("input", synchronizeAnalysisScopeState\)/);
  assert.match(app, /addEventListener\("change"/);
  assert.match(app, /addEventListener\("reset"/);
  assert.match(app, /farRedRequestFields\(analysisScopeSelect\.value/);
});

test("Proposed spectral basis is visible only for Proposed multispectral analysis", () => {
  const attributes = new Map();
  const field = {
    hidden: true,
    setAttribute(name, value) { attributes.set(name, value); },
  };
  const select = { value: CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS, disabled: true };

  assert.equal(synchronizeProposedSpectralBasis({
    system: "proposed",
    scope: MULTISPECTRAL_FSPM_SCOPE,
    field,
    select,
  }), true);
  assert.equal(field.hidden, false);
  assert.equal(select.disabled, false);

  assert.equal(synchronizeProposedSpectralBasis({
    system: "conventional",
    scope: MULTISPECTRAL_FSPM_SCOPE,
    field,
    select,
  }), false);
  assert.equal(field.hidden, true);
  assert.equal(attributes.get("aria-hidden"), "true");
  assert.equal(select.disabled, true);
  assert.equal(select.value, NATIVE_PROPOSED_SPECTRAL_BASIS);

  select.value = CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS;
  synchronizeProposedSpectralBasis({
    system: "proposed",
    scope: "baseline_ppfd",
    field,
    select,
  });
  assert.equal(select.value, NATIVE_PROPOSED_SPECTRAL_BASIS);
});

test("only Proposed multispectral requests carry a validated spectral basis", () => {
  assert.deepEqual(
    proposedSpectralBasisRequestFields(
      "proposed",
      MULTISPECTRAL_FSPM_SCOPE,
      CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS,
    ),
    { spectral_basis: CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS },
  );
  assert.deepEqual(
    proposedSpectralBasisRequestFields(
      "proposed", "baseline_ppfd", CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS,
    ),
    {},
  );
  assert.deepEqual(
    proposedSpectralBasisRequestFields(
      "hps", MULTISPECTRAL_FSPM_SCOPE, CONVENTIONAL_LED_CONTROL_SPECTRAL_BASIS,
    ),
    {},
  );
});

test("Rolling Bench is the Conventional default and survives system switches", () => {
  const attributes = new Map();
  const field = {
    hidden: true,
    setAttribute(name, value) { attributes.set(name, value); },
  };
  const select = {value: "", disabled: true};

  assert.equal(synchronizeLayoutMode({
    system: "conventional", field, select,
  }), true);
  assert.equal(field.hidden, false);
  assert.equal(attributes.get("aria-hidden"), "false");
  assert.equal(select.disabled, false);
  assert.equal(select.value, ROLLING_BENCH_LAYOUT_MODE);

  select.value = ROLLING_BENCH_LAYOUT_MODE;
  assert.equal(synchronizeLayoutMode({
    system: "proposed", field, select,
  }), false);
  assert.equal(field.hidden, true);
  assert.equal(attributes.get("aria-hidden"), "true");
  assert.equal(select.disabled, true);
  assert.equal(select.value, ROLLING_BENCH_LAYOUT_MODE);
  assert.deepEqual(
    layoutModeRequestFields("proposed", select.value),
    {},
  );
});

test("only Conventional requests carry a visible layout mode", () => {
  assert.deepEqual(
    layoutModeRequestFields("conventional", ROLLING_BENCH_LAYOUT_MODE),
    {layout_mode: ROLLING_BENCH_LAYOUT_MODE},
  );
  assert.throws(
    () => layoutModeRequestFields("conventional", "system_default"),
    /Unsupported Conventional fixture layout/,
  );
  assert.deepEqual(
    layoutModeRequestFields("hps", ROLLING_BENCH_LAYOUT_MODE),
    {},
  );

  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const app = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  assert.match(html, /id="layout-mode-field" aria-hidden="true" hidden/);
  assert.match(html, /option value="practical">Practical Coverage/);
  assert.match(html, /option value="rolling_bench" selected>Rolling Bench/);
  assert.doesNotMatch(html, /System default|system_default/);
  assert.doesNotMatch(html, /Rolling Bench uses a centered rigid 48 in fixture pitch/);
  const styles = readFileSync(
    new URL("../src/fspm_optics/resources/web/styles.css", import.meta.url),
    "utf8",
  );
  assert.match(styles, /\.field-group\[hidden\] \{ display: none; \}/);
  assert.match(app, /synchronizeLayoutMode\(/);
  assert.match(app, /layoutModeRequestFields\(systemSelect\.value/);
});

test("Module arrangement stays hidden and submits Reduced by one ring by default", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const app = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  assert.match(
    html,
    /id="proposed-ring-mode-field" aria-hidden="true" hidden/,
  );
  assert.match(html, /<span>Module arrangement<\/span>/);
  assert.match(html, /option value="full">Full ring/);
  assert.match(
    html,
    /option value="reduced_one_ring"[^>]*>Reduced by one ring/,
  );
  assert.match(html, /option value="reduced_one_ring" selected>Reduced by one ring/);

  const attributes = new Map();
  const field = {
    hidden: true,
    setAttribute(name, value) { attributes.set(name, value); },
  };
  const select = {value: REDUCED_ONE_RING, disabled: true};
  assert.equal(synchronizeProposedRingMode({
    system: "proposed", field, select,
  }), true);
  assert.equal(field.hidden, true);
  assert.equal(attributes.get("aria-hidden"), "true");
  assert.equal(select.disabled, true);
  assert.equal(select.value, REDUCED_ONE_RING);
  assert.deepEqual(
    proposedRingModeRequestFields("proposed", select.value),
    {proposed_ring_mode: REDUCED_ONE_RING},
  );

  assert.equal(synchronizeProposedRingMode({
    system: "conventional", field, select,
  }), false);
  assert.equal(field.hidden, true);
  assert.equal(select.disabled, true);
  assert.equal(select.value, REDUCED_ONE_RING);
  assert.deepEqual(
    proposedRingModeRequestFields("conventional", REDUCED_ONE_RING),
    {},
  );
  assert.deepEqual(
    proposedRingModeRequestFields("hps", REDUCED_ONE_RING),
    {},
  );
  assert.match(app, /synchronizeProposedRingMode\(/);
  assert.match(app, /proposedRingModeRequestFields\([\s\S]*systemSelect\.value,[\s\S]*REDUCED_ONE_RING/);
  assert.deepEqual(
    proposedRingModeRequestFields("proposed", FULL_RING),
    {proposed_ring_mode: FULL_RING},
  );
});

test("Proposed module arrangement rejects unsupported browser values", () => {
  assert.throws(
    () => proposedRingModeRequestFields("proposed", "reduced"),
    /Unsupported Proposed module arrangement/,
  );
});

test("Proposed module arrangement identity comparison ignores JSON key order", () => {
  const metricsLayout = {
    fixture_policy_id: "proposed_standalone_module_instances_with_alignment_lattice_v2",
    mode: "standalone_modules",
    module_pattern_id: "centered_square_reduced_one_ring_v1",
    ring_mode: REDUCED_ONE_RING,
  };
  const resultLayout = {
    mode: "standalone_modules",
    fixture_policy_id: "proposed_standalone_module_instances_with_alignment_lattice_v2",
    ring_mode: REDUCED_ONE_RING,
    module_pattern_id: "centered_square_reduced_one_ring_v1",
  };

  assert.notEqual(JSON.stringify(metricsLayout), JSON.stringify(resultLayout));
  assert.equal(
    proposedLayoutIdentitiesAgree(metricsLayout, resultLayout),
    true,
  );
  assert.equal(proposedLayoutIdentitiesAgree(undefined, undefined), true);
  assert.equal(proposedLayoutIdentitiesAgree(metricsLayout, undefined), false);
  assert.equal(
    proposedLayoutIdentitiesAgree(metricsLayout, {
      ...resultLayout,
      module_pattern_id: "centered_square_full_v1",
    }),
    false,
  );
  assert.equal(
    proposedLayoutIdentitiesAgree({
      ...metricsLayout,
      fixture_policy_id: "proposed_linear_fixture_assemblies_v1",
    }, resultLayout),
    false,
  );

  const resultView = readFileSync(
    new URL("../src/fspm_optics/resources/web/result-view.js", import.meta.url),
    "utf8",
  );
  assert.match(resultView, /proposedLayoutIdentitiesAgree\(/);
  assert.doesNotMatch(resultView, /JSON\.stringify\(metrics\.proposed_layout/);
});

test("Basis-Matrix control stays hidden and defaults to Uniform module dimming", () => {
  const attributes = new Map();
  const field = {
    hidden: true,
    setAttribute(name, value) { attributes.set(name, value); },
  };
  const input = {checked: false, disabled: true};

  assert.equal(synchronizeProposedControlMode({
    system: "proposed", field, input,
  }), true);
  assert.equal(field.hidden, true);
  assert.equal(attributes.get("aria-hidden"), "true");
  assert.equal(input.disabled, true);
  assert.equal(input.checked, true);
  assert.deepEqual(
    proposedControlModeRequestFields("proposed", input.checked),
    {proposed_control_mode: UNIFORM_MODULE_DIMMING},
  );

  input.checked = true;
  assert.deepEqual(
    proposedControlModeRequestFields("proposed", input.checked),
    {proposed_control_mode: UNIFORM_MODULE_DIMMING},
  );
  assert.equal(synchronizeProposedControlMode({
    system: "conventional", field, input,
  }), false);
  assert.equal(field.hidden, true);
  assert.equal(input.disabled, true);
  assert.equal(input.checked, false);
  assert.deepEqual(
    proposedControlModeRequestFields("conventional", true),
    {},
  );
  assert.deepEqual(
    proposedControlModeRequestFields("proposed", false),
    {proposed_control_mode: BASIS_MATRIX_OPTIMIZED},
  );
});

test("Proposed control-mode UI keeps the hidden compatibility toggle checked", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const app = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  assert.match(html, /<span>Disable Basis-Matrix Solver<\/span>/);
  assert.match(
    html,
    /id="disable-basis-matrix-solver" type="checkbox" checked disabled/,
  );
  assert.match(app, /synchronizeProposedControlMode\(/);
  assert.match(app, /proposedControlModeRequestFields\(/);
  const resultView = readFileSync(
    new URL("../src/fspm_optics/resources/web/result-view.js", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(resultView, /data-metric="proposed-control-mode"/);
});

test("result control identity ignores JSON property order but rejects mode changes", () => {
  const metricsControl = {
    basis_matrix_solver_enabled: false,
    label: "Uniform module dimming",
    mode: UNIFORM_MODULE_DIMMING,
    module_count: 61,
  };
  const resultControl = {
    mode: UNIFORM_MODULE_DIMMING,
    module_count: 61,
    label: "Uniform module dimming",
    basis_matrix_solver_enabled: false,
  };

  assert.equal(
    proposedControlModesAgree(metricsControl, resultControl),
    true,
  );
  assert.equal(
    proposedControlModesAgree(metricsControl, {
      ...resultControl,
      mode: BASIS_MATRIX_OPTIMIZED,
      basis_matrix_solver_enabled: true,
    }),
    false,
  );
  assert.equal(proposedControlModesAgree(undefined, undefined), true);
  assert.equal(proposedControlModesAgree(metricsControl, undefined), false);
});

test("execution mode defaults live and precomputed reuses the result pipeline", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/web/index.html", import.meta.url),
    "utf8",
  );
  const app = readFileSync(
    new URL("../src/fspm_optics/resources/web/app.js", import.meta.url),
    "utf8",
  );
  const resultView = readFileSync(
    new URL("../src/fspm_optics/resources/web/result-view.js", import.meta.url),
    "utf8",
  );

  assert.match(html, /<span>Execution Mode<\/span>/);
  assert.match(html, /value="live" selected>Live Simulation<\/option>/);
  assert.match(html, /value="precomputed">Precomputed Playback<\/option>/);
  assert.match(app, /\/api\/precomputed\/availability/);
  assert.match(app, /\/api\/precomputed\/playbacks/);
  assert.match(app, /Load Precomputed Result/);
  assert.match(app, /await loadResults\(body\.result\)/);
  assert.match(
    app,
    /input\[name="quality"\]\[value="standard"\]'\)\.checked = true/,
  );
  assert.match(app, /targetField\.hidden = hps/);
  assert.match(app, /Unavailable —/);
  assert.match(resultView, /if \(quarterTurns !== 0\)/);
  assert.doesNotMatch(resultView, /rotate\(-90deg\)/);
  assert.doesNotMatch(resultView, /heatmapImage\.style\.transform/);
  assert.match(app, /Canonical display \$\{displayRoom\.length\} ×/);
});
