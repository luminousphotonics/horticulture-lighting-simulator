// @ts-check

const { expect, test } = require("@playwright/test");

const DEMO_GUIDE_STORAGE_KEY = "horticulture-lighting-simulator.demoGuide.preference.v2";

function tinyGlb() {
  const jsonText = JSON.stringify({ asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [] }], nodes: [] });
  const paddedLength = Math.ceil(jsonText.length / 4) * 4;
  const jsonChunk = Buffer.alloc(paddedLength, 0x20);
  jsonChunk.write(jsonText, 0, "utf8");
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(20 + jsonChunk.length, 8);
  const chunkHeader = Buffer.alloc(8);
  chunkHeader.writeUInt32LE(jsonChunk.length, 0);
  chunkHeader.writeUInt32LE(0x4e4f534a, 4);
  return Buffer.concat([header, chunkHeader, jsonChunk]);
}

function heatmapMetadata(mode, modeLabel) {
  return {
    schema_version: 1,
    mode,
    mode_label: modeLabel,
    units: "umol/m2/s",
    grid_width: 2,
    grid_height: 2,
    value_count: 4,
    bounds_m: {
      x_min: -1.524,
      x_max: 1.524,
      y_min: -1.524,
      y_max: 1.524,
      z_m: 0.005,
    },
    min_ppfd: 400,
    max_ppfd: 700,
    mean_ppfd: 550,
    target_ppfd: 1000,
    encoding: "float32-le",
    colormap: {
      name: "viridis",
      source: "matplotlib",
      normalization: "linear-clamped",
    },
    color_scale: {
      vmin: 350,
      vmax: 750,
      source: "simulation-visualization-mean-ppfd",
      clamp: true,
    },
    orientation: {
      plane: "xy",
      column_axis: "x",
      row_axis: "y",
      column_order: "ascending",
      row_order: "ascending",
      storage_order: "row-major",
      value_index: "row * grid_width + column",
    },
    warnings: [],
  };
}

function directFixtureScene({ mode, modeLabel, system, assetRoot }) {
  return {
    schema_version: 3,
    system,
    mode,
    mode_label: modeLabel,
    display_name: modeLabel,
    units: "meters",
    source_units: "millimeters",
    viewer_scale: 0.001,
    placement_strategy: "single_fixture_center",
    axis_mapping: {
      cad_horizontal: ["x", "z"],
      cad_vertical: "y",
      layout_horizontal: ["x", "y"],
      layout_vertical: "z",
    },
    room: { length_m: 3.048, width_m: 3.048, mount_z_m: 0.4572 },
    assets: {
      manifest: `/static/viewer/${assetRoot}/manifest.json`,
    },
    module_assets: {
      fixture: {
        high: `/static/viewer/${assetRoot}/fixture.high.glb`,
        medium: `/static/viewer/${assetRoot}/fixture.medium.glb`,
        proxy: `/static/viewer/${assetRoot}/fixture.proxy.glb`,
      },
    },
    instances: [
      {
        id: "fixture-0001",
        layout_type: "single_fixture_center",
        orient: "yaw_deg:0",
        module_count: 1,
        shape: "fixture",
        asset_key: "fixture",
        asset_fallback_key: null,
        points: [
          { x: -0.6, y: -0.45, z: 0.4572 },
          { x: 0.6, y: -0.45, z: 0.4572 },
          { x: 0.6, y: 0.45, z: 0.4572 },
          { x: -0.6, y: 0.45, z: 0.4572 },
        ],
        position: { x: 0, y: 0, z: 0.4572 },
        yaw_deg: 0,
        layout_source: {
          mode,
          path: mode === "Competitor" ? "runtime_state/spydr3_layout.json" : "runtime_state/hps_layout.json",
          fixture_index: 0,
          position_fields: ["cx", "cy", "z"],
          orientation_source: mode === "Competitor" ? "rot_deg" : "lamp_line",
        },
        warnings: [],
      },
    ],
    fixture_counts_by_layout_type: { single_fixture_center: 1 },
    fixture_counts_by_asset_key: { fixture: 1 },
    missing_asset_keys: [],
    asset_fallbacks_used: [],
    warnings: [],
  };
}

async function routeCompletedAssemblyRun(page, scenePayload) {
  await page.route("**/radiance-api/health**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await page.route("**/radiance-api/radiance/run**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ artifact_token: "assembly-token", job_id: "assembly-job" }),
    });
  });
  await page.route("**/radiance-api/jobs/assembly-job/tail**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ lines: ["done"], next_cursor: 1, done: true, status: "completed" }),
    });
  });
  await page.route("**/radiance-api/radiance/images**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/radiance-api/radiance/metrics**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ metrics: { mean: 1000 }, cost_estimate: null }),
    });
  });
  await page.route("**/radiance-api/radiance/assembly-scene**", async (route) => {
    if (route.request().method() === "HEAD") {
      await route.fulfill({ status: 200 });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(scenePayload),
    });
  });
  await page.route(/\/radiance-api\/radiance\/assembly-photometric-layer\?/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(heatmapMetadata(scenePayload.mode, scenePayload.mode_label)),
    });
  });
  await page.route(/\/radiance-api\/radiance\/assembly-photometric-layer\.bin\?/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      body: Buffer.from(new Float32Array([400, 500, 600, 700]).buffer),
    });
  });
  await page.route(/\/static\/viewer\/.*\/fixture\.(?:high|medium|proxy)\.glb$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "model/gltf-binary",
      body: tinyGlb(),
    });
  });
}

function liveRuntimeModalHtml() {
  return `
    <div id="live-runtime-modal" class="radiance-modal hidden" role="dialog" aria-modal="true" aria-labelledby="live-runtime-title">
      <div class="radiance-modal__card radiance-modal__card--runtime">
        <div class="radiance-modal__header radiance-modal__header--metrics">
          <div>
            <p id="live-runtime-eyebrow" class="radiance-modal__eyebrow">Live Runtime</p>
            <strong id="live-runtime-title">Live mode setup</strong>
          </div>
          <button id="live-runtime-close" class="btn btn--secondary" type="button">Close</button>
        </div>
        <div class="radiance-runtime">
          <p id="live-runtime-message" class="radiance-runtime__message"></p>
          <dl id="live-runtime-facts" class="radiance-missing-bundle__facts"></dl>
          <div id="live-runtime-diagnostics" class="radiance-runtime__diagnostics"></div>
          <p id="live-runtime-note" class="radiance-runtime__note">Terminal export commands are temporary and affect only the current terminal and dev server process. Restart the dev server after changing runtime environment variables.</p>
          <div id="live-runtime-commands" class="radiance-runtime__commands"></div>
          <div class="radiance-modal__actions">
            <button id="live-runtime-precomputed" class="btn btn--primary" type="button">Switch to Precomputed</button>
            <button id="live-runtime-recheck" class="btn btn--secondary" type="button">Re-check</button>
            <button id="live-runtime-cancel" class="btn btn--secondary" type="button">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

async function routeLiveSimulatorPage(page) {
  await page.route("**/radiance-simulator", async (route) => {
    const response = await route.fetch();
    let html = await response.text();
    html = html
      .replace('id="rad-sim-mode-field" hidden', 'id="rad-sim-mode-field"')
      .replace(
        '<option value="precomputed" selected>Precomputed</option>',
        '<option value="precomputed" selected>Precomputed</option><option value="live_docker">Live - Docker</option><option value="live_local">Live - Local Radiance</option>',
      )
      .replace('"showLiveModes": false', '"showLiveModes": true')
      .replace('<div id="electrical-modal"', `${liveRuntimeModalHtml()}<div id="electrical-modal"`);
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      body: html,
    });
  });
}

async function routeBasicRadianceBackend(page, getStatusPayload) {
  await page.route("**/radiance-api/health**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await page.route("**/radiance-api/radiance/images**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/radiance-api/radiance/runtime/status**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(getStatusPayload()),
    });
  });
}

function runtimeStatus(overrides = {}) {
  return {
    live_execution_enabled: true,
    live_supported_modes: ["SMD"],
    live_unsupported_mode_message: "Live Radiance mode is currently available only for the Proposed LED System in the public GitHub release. Conventional LED and 1000W HPS rely on IES photometry assets that are not included in the public repository, so those systems are available through precomputed mode only. Switch to Precomputed mode to view the Conventional LED or HPS simulation, 3D assembly, and PPFD heatmap.",
    modes: {
      precomputed: { available: true },
      live_docker: {
        available: true,
        reason: null,
        supported_lighting_modes: ["SMD"],
        docker_cli: "/usr/bin/docker",
        daemon_available: true,
        image_available: true,
        can_build_image: true,
        setup_commands: [],
      },
      live_local: {
        available: true,
        reason: null,
        supported_lighting_modes: ["SMD"],
        radiance_home: "/opt/radiance",
        radiance_bin_dir: "/opt/radiance/bin",
        radiance_lib_dir: "/opt/radiance/lib",
        detected_executables: { oconv: "/opt/radiance/bin/oconv", rtrace: "/opt/radiance/bin/rtrace", rcontrib: "/opt/radiance/bin/rcontrib" },
        missing_executables: [],
        env_values: {},
        setup_commands: [],
      },
    },
    ...overrides,
  };
}

test("public pages load with CSP and no console errors", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      const text = message.text();
      if (!/status of (404|503)/.test(text)) {
        errors.push(text);
      }
    }
  });
  await page.route("**/radiance-api/health", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ status: "not_ready" }) });
  });

  for (const path of ["/", "/radiance-simulator"]) {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page.locator("body")).toBeVisible();
    expect(response?.headers()["content-security-policy"]).toContain("script-src 'self'");
  }

  expect(errors).toEqual([]);
});

test("live runtime setup modals open for unavailable Proposed runtimes", async ({ page }) => {
  let status = runtimeStatus({
    modes: {
      ...runtimeStatus().modes,
      live_local: {
        ...runtimeStatus().modes.live_local,
        available: false,
        reason: "missing_executables",
        missing_executables: ["oconv"],
        setup_commands: [{ label: "Use Radiance from PATH", command: "export RADIANCE_BIN_DIR=\"$(dirname \"$(command -v oconv)\")\"" }],
      },
    },
  });
  await routeLiveSimulatorPage(page);
  await routeBasicRadianceBackend(page, () => status);
  await page.goto("/radiance-simulator");

  await page.locator("#rad-sim-mode").selectOption("live_local");
  await expect(page.getByRole("dialog", { name: "Live Local Radiance is not ready yet" })).toBeVisible();
  await expect(page.getByText("Missing executables: oconv")).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Re-check" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();

  status = runtimeStatus({
    modes: {
      ...runtimeStatus().modes,
      live_docker: {
        ...runtimeStatus().modes.live_docker,
        available: false,
        reason: "docker_daemon_unreachable",
        daemon_available: false,
        image_available: false,
        setup_commands: [{ label: "Clear Docker host override", command: "unset DOCKER_HOST\npython -m rad_rebuild.dev --live" }],
      },
    },
  });
  await page.locator("#rad-sim-mode").selectOption("live_docker");
  await expect(page.getByRole("dialog", { name: "Live Docker is not ready yet" })).toBeVisible();
  await expect(page.getByText("Daemon: Not reachable")).toBeVisible();
  await expect(page.locator("#rad-sim-mode")).toHaveValue("precomputed");
});

test("live mode selection explains unsupported public lighting systems", async ({ page }) => {
  await routeLiveSimulatorPage(page);
  await routeBasicRadianceBackend(page, () => runtimeStatus());
  await page.goto("/radiance-simulator");

  for (const system of ["Competitor", "1000W HPS"]) {
    await page.locator("#rad-mode").selectOption(system);
    await page.locator("#rad-sim-mode").selectOption("live_local");
    const dialog = page.getByRole("dialog", { name: "Use Precomputed for this system" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator("#live-runtime-message")).toContainText("IES photometry assets");
    await expect(page.getByRole("button", { name: "Switch to Precomputed" })).toBeVisible();
    await page.getByRole("button", { name: "Switch to Precomputed" }).click();
    await expect(page.locator("#rad-sim-mode")).toHaveValue("precomputed");
  }
});

test("precomputed stays quiet and ready Proposed live mode is allowed", async ({ page }) => {
  await routeLiveSimulatorPage(page);
  await routeBasicRadianceBackend(page, () => runtimeStatus());
  await page.goto("/radiance-simulator");

  await page.locator("#rad-sim-mode").selectOption("precomputed");
  await expect(page.locator("#live-runtime-modal")).toBeHidden();

  await page.locator("#rad-mode").selectOption("SMD");
  await page.locator("#rad-sim-mode").selectOption("live_local");
  await expect(page.locator("#live-runtime-modal")).toBeHidden();
  await expect(page.locator("#rad-sim-mode")).toHaveValue("live_local");
});

test("root opens simulator and retired layout page redirects", async ({ page }) => {
  const rootResponse = await page.goto("/");
  expect(rootResponse?.status()).toBe(200);
  await expect(page.locator("#radiance-form")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run + Visualize" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("[data-theme-toggle] .theme-button__label")).toHaveText("Dark mode");
  await expect(page.locator(".navbar__links .navbar__link", { hasText: "Simulator" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  const legacyResponse = await page.goto("/layout-generator");
  expect(legacyResponse?.status()).toBe(200);
  expect(page.url()).toContain("/radiance-simulator");
  await expect(page.locator("#radiance-form")).toBeVisible();
});

test("demo guide first-run prompt can be dismissed without storing a preference", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      const text = message.text();
      if (!/status of (404|503)/.test(text)) {
        errors.push(text);
      }
    }
  });
  await page.route("**/radiance-api/health", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ status: "not_ready" }) });
  });

  await page.goto("/radiance-simulator");
  const prompt = page.getByRole("dialog", { name: "Would you like a quick walkthrough?" });
  await expect(prompt).toBeVisible();
  await expect(prompt).toContainText("run a precomputed simulation");
  await expect(prompt).toContainText("PPFD heatmap");
  await expect(prompt.getByRole("button", { name: "Start walkthrough" })).toBeVisible();
  await expect(prompt.getByRole("button", { name: "Not now" })).toBeVisible();
  await expect(prompt.getByLabel("Remember my choice")).toBeVisible();

  await prompt.getByRole("button", { name: "Not now" }).click();
  await expect(prompt).toBeHidden();
  await expect(page.locator("#demo-guide-root")).toBeHidden();
  await expect(page.evaluate((key) => window.localStorage.getItem(key), DEMO_GUIDE_STORAGE_KEY)).resolves.toBeNull();
  expect(errors).toEqual([]);
});

test("demo guide remembered dismissal suppresses future first-run prompts", async ({ page }) => {
  await page.goto("/radiance-simulator");
  const prompt = page.getByRole("dialog", { name: "Would you like a quick walkthrough?" });
  await expect(prompt).toBeVisible();

  await prompt.getByLabel("Remember my choice").check();
  await prompt.getByRole("button", { name: "Not now" }).click();
  await expect(prompt).toBeHidden();
  await expect(page.evaluate((key) => window.localStorage.getItem(key), DEMO_GUIDE_STORAGE_KEY)).resolves.toBe("dismissed");

  await page.reload();
  await expect(page.locator("#demo-guide-root")).toBeHidden();
  await expect(page.getByRole("dialog", { name: "Would you like a quick walkthrough?" })).toBeHidden();
});

test("demo guide starts an interactive walkthrough with navigation controls", async ({ page }) => {
  await page.goto("/radiance-simulator");
  const prompt = page.getByRole("dialog", { name: "Would you like a quick walkthrough?" });
  await prompt.getByRole("button", { name: "Start walkthrough" }).click();

  const guide = page.getByRole("dialog", { name: "Choose a system" });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("Step 1 of 7");
  await expect(guide.getByRole("button", { name: "Back" })).toBeDisabled();
  await expect(guide.getByRole("button", { name: "Skip walkthrough" })).toBeVisible();
  await expect(guide.getByRole("button", { name: "Next walkthrough step" })).toBeVisible();
  await expect(guide.getByLabel("Remember my choice")).toBeHidden();
  await expect(page.locator(".demo-guide__highlight")).toBeVisible();

  await guide.getByRole("button", { name: "Next walkthrough step" }).click();
  await expect(page.getByRole("dialog", { name: "Choose room size and target" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Back" })).toBeEnabled();
  await page.getByRole("button", { name: "Back" }).click();
  await expect(page.getByRole("dialog", { name: "Choose a system" })).toBeVisible();

  for (let index = 0; index < 6; index += 1) {
    await page.getByRole("button", { name: /Next walkthrough step|Done with walkthrough/ }).click();
  }
  const finalGuide = page.getByRole("dialog", { name: "View technical context" });
  await expect(finalGuide).toBeVisible();
  await expect(finalGuide.getByRole("button", { name: "Done with walkthrough" })).toBeVisible();
  await finalGuide.getByRole("button", { name: "Done with walkthrough" }).click();
  await expect(page.locator("#demo-guide-root")).toBeHidden();
  await expect(page.evaluate((key) => window.localStorage.getItem(key), DEMO_GUIDE_STORAGE_KEY)).resolves.toBeNull();

  await page.reload();
  await expect(page.getByRole("dialog", { name: "Would you like a quick walkthrough?" })).toBeVisible();
});

test("demo guide remembered start suppresses future first-run prompts", async ({ page }) => {
  await page.goto("/radiance-simulator");
  const prompt = page.getByRole("dialog", { name: "Would you like a quick walkthrough?" });
  await prompt.getByLabel("Remember my choice").check();
  await prompt.getByRole("button", { name: "Start walkthrough" }).click();

  await expect(page.getByRole("dialog", { name: "Choose a system" })).toBeVisible();
  await expect(page.evaluate((key) => window.localStorage.getItem(key), DEMO_GUIDE_STORAGE_KEY)).resolves.toBe("completed");

  await page.reload();
  await expect(page.locator("#demo-guide-root")).toBeHidden();
  await expect(page.getByRole("dialog", { name: "Would you like a quick walkthrough?" })).toBeHidden();
});

test("about modal includes manual demo guide relaunch", async ({ page }, testInfo) => {
  await page.addInitScript((key) => {
    window.localStorage.setItem(key, "dismissed");
  }, DEMO_GUIDE_STORAGE_KEY);
  await page.goto("/radiance-simulator");
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  }

  await page.getByRole("button", { name: "About" }).click();
  const aboutDialog = page.getByRole("dialog", { name: "About Horticulture Lighting Simulator" });
  await expect(aboutDialog).toBeVisible();
  const demoButton = aboutDialog.getByRole("button", { name: "Demo guide" });
  await expect(demoButton).toBeVisible();
  await demoButton.click();

  await expect(aboutDialog).toBeHidden();
  await expect(page.getByRole("dialog", { name: "Choose a system" })).toBeVisible();
});

test("about modal and GitHub actions are accessible", async ({ page }, testInfo) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      const text = message.text();
      if (!/status of (404|503)/.test(text)) {
        errors.push(text);
      }
    }
  });

  await page.goto("/radiance-simulator");
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Toggle navigation" }).click();
  }
  const aboutButton = page.getByRole("button", { name: "About" });
  await expect(aboutButton).toBeVisible();

  const navGithub = page.locator(".navbar__links a", { hasText: "GitHub" });
  await expect(navGithub).toHaveAttribute("href", "https://github.com/luminousphotonics/horticulture-lighting-simulator");
  await expect(navGithub).toHaveAttribute("target", "_blank");
  await expect(navGithub).toHaveAttribute("rel", "noopener noreferrer");

  await aboutButton.click();
  const dialog = page.getByRole("dialog", { name: "About Horticulture Lighting Simulator" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Engineering highlights")).toBeVisible();
  await expect(dialog.getByText("Created by Austin Rouse.")).toBeVisible();
  const patentLink = dialog.getByRole("link", { name: "Optimized LED Lighting Array for Horticultural Applications" });
  await expect(patentLink).toHaveAttribute("href", "https://patents.google.com/patent/US10687478B2/en");
  await expect(patentLink).toHaveAttribute("target", "_blank");
  await expect(patentLink).toHaveAttribute("rel", "noopener noreferrer");
  const modalGithub = dialog.getByRole("link", { name: "View GitHub" });
  await expect(modalGithub).toHaveAttribute("href", "https://github.com/luminousphotonics/horticulture-lighting-simulator");
  await expect(modalGithub).toHaveAttribute("target", "_blank");
  await expect(modalGithub).toHaveAttribute("rel", "noopener noreferrer");

  await dialog.getByRole("button", { name: "Close" }).last().click();
  await expect(dialog).toBeHidden();
  expect(errors).toEqual([]);
});

test("mobile navigation closes on Escape and focus leave", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Toggle navigation" });

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");

  await toggle.click();
  await page.locator("[data-theme-toggle]").focus();
  await page.keyboard.press("Tab");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("radiance dialog traps focus and restores opener", async ({ page }) => {
  await page.goto("/");
  const opener = page.getByRole("button", { name: "Explain the Metrics" });
  await opener.click();

  const dialog = page.getByRole("dialog", { name: "How to read the lighting metrics" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "Close" }).first()).toBeFocused();

  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Close" }).first()).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("radiance assembly button opens 3D viewer after completed SMD run", async ({ page }, testInfo) => {
  const errors = [];
  const isMobile = testInfo.project.name === "mobile";
  let heatmapMetadataRequests = 0;
  let heatmapBinaryRequests = 0;
  page.on("console", (message) => {
    if (message.type() === "error") {
      errors.push(message.text());
    }
  });
  await page.route("**/radiance-api/health**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
  await page.route("**/radiance-api/radiance/run**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ artifact_token: "assembly-token", job_id: "assembly-job" }),
    });
  });
  await page.route("**/radiance-api/jobs/assembly-job/tail**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ lines: ["done"], next_cursor: 1, done: true, status: "completed" }),
    });
  });
  await page.route("**/radiance-api/radiance/images**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/radiance-api/radiance/metrics**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ metrics: { mean: 1000 }, cost_estimate: null }),
    });
  });
  await page.route("**/radiance-api/radiance/assembly-scene**", async (route) => {
    if (route.request().method() === "HEAD") {
      await route.fulfill({ status: 200 });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: 3,
        system: "proposed_led_system",
        mode: "SMD",
        mode_label: "Proposed LED System",
        display_name: "Proposed LED System",
        units: "meters",
        source_units: "millimeters",
        viewer_scale: 0.001,
        axis_mapping: {
          cad_horizontal: ["x", "z"],
          cad_vertical: "y",
          layout_horizontal: ["x", "y"],
          layout_vertical: "z",
        },
        room: { length_m: 3.048, width_m: 3.048, mount_z_m: 0.4572 },
        plants: {
          schema: "rad_rebuild.fspm.plants.viewer.v1",
          units: "meters",
          config: {
            seed: 7,
            plant_grid_rows: 1,
            plant_grid_columns: 1,
            plant_spacing_m: 0.4,
            plant_height_m: 0.2,
            canopy_radius_m: 0.22,
            leaf_count_per_plant: 1,
            growth_stage: 0.7,
          },
          material: {
            id: "plant_leaf_material",
            reflectance: 0.22,
            transmittance: 0.08,
            absorptance: 0.7,
          },
          plants: [
            {
              plant_id: "plant_r000_c000",
              row: 0,
              column: 0,
              center_m: [0, 0, 0],
              leaves: [
                {
                  plant_id: "plant_r000_c000",
                  leaf_id: "plant_r000_c000_leaf_000",
                  radiance_material_id: "plant_leaf_material",
                  mesh: {
                    vertices: [
                      [-0.1, -0.05, 0.02],
                      [0.1, -0.05, 0.03],
                      [0.0, 0.16, 0.06],
                    ],
                    faces: [[0, 1, 2]],
                  },
                },
              ],
            },
          ],
        },
        assets: {
          manifest: "/static/viewer/proposed_led_system/manifest.json",
          anchors: "/static/viewer/proposed_led_system/anchors.json",
        },
        module_assets: {
          centerpiece: {
            high: "/static/viewer/proposed_led_system/fixture_centerpiece.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
          },
          linear2: {
            high: "/static/viewer/proposed_led_system/fixture_L2_linear.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
          },
          linear3_linear: {
            high: "/static/viewer/proposed_led_system/fixture_L3_linear.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
          },
          linear3_corner: {
            high: "/static/viewer/proposed_led_system/fixture_L3.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
            optional: true,
            fallback_asset_key: "linear3_linear",
          },
          linear4_linear: {
            high: "/static/viewer/proposed_led_system/fixture_L4_linear.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
          },
          l4_corner: {
            high: "/static/viewer/proposed_led_system/fixture_L4.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
          },
          l4_reverse_corner: {
            high: "/static/viewer/proposed_led_system/fixture_L4_reverse.high.glb",
            medium: null,
            proxy: null,
            anchor_source: "module_nodes",
          },
        },
        instances: [
          {
            id: "fixture-0001",
            layout_type: "centerpiece",
            orient: null,
            module_count: 5,
            shape: "centerpiece",
            asset_key: "centerpiece",
            asset_fallback_key: null,
            points: [
              { x: 0, y: 0, z: 0.4572 },
              { x: 0.28, y: -0.28, z: 0.4572 },
              { x: 0.28, y: 0.28, z: 0.4572 },
              { x: -0.28, y: -0.28, z: 0.4572 },
              { x: -0.28, y: 0.28, z: 0.4572 },
            ],
            warnings: [],
          },
          {
            id: "fixture-0002",
            layout_type: "linear2",
            orient: "o1",
            module_count: 2,
            shape: "linear",
            asset_key: "linear2",
            asset_fallback_key: null,
            points: [
              { x: -1.0, y: -0.8, z: 0.4572 },
              { x: -0.5, y: -0.8, z: 0.4572 },
            ],
            warnings: [],
          },
          {
            id: "fixture-0003",
            layout_type: "linear3",
            orient: "o1",
            module_count: 3,
            shape: "linear",
            asset_key: "linear3_linear",
            asset_fallback_key: null,
            points: [
              { x: 0.5, y: -0.9, z: 0.4572 },
              { x: 1.0, y: -0.9, z: 0.4572 },
              { x: 1.5, y: -0.9, z: 0.4572 },
            ],
            warnings: [],
          },
          {
            id: "fixture-0004",
            layout_type: "linear3",
            orient: "o2",
            module_count: 3,
            shape: "corner",
            asset_key: "linear3_corner",
            asset_fallback_key: "linear3_linear",
            points: [
              { x: -0.3, y: 0, z: 0.4572 },
              { x: -0.3, y: 0.2, z: 0.4572 },
              { x: -0.1, y: 0.2, z: 0.4572 },
            ],
            warnings: ["optional fixture asset 'linear3_corner' is missing; using 'linear3_linear'."],
          },
          {
            id: "fixture-0005",
            layout_type: "L",
            orient: "o1",
            module_count: 4,
            shape: "linear",
            asset_key: "linear4_linear",
            asset_fallback_key: null,
            points: [
              { x: 0.1, y: 0, z: 0.4572 },
              { x: 0.3, y: 0, z: 0.4572 },
              { x: 0.5, y: 0, z: 0.4572 },
              { x: 0.7, y: 0, z: 0.4572 },
            ],
            warnings: [],
          },
          {
            id: "fixture-0006",
            layout_type: "L",
            orient: "o3",
            module_count: 4,
            shape: "corner",
            asset_key: "l4_corner",
            asset_fallback_key: null,
            points: [
              { x: -1.2, y: 0.7, z: 0.4572 },
              { x: -0.7, y: 0.7, z: 0.4572 },
              { x: -0.2, y: 0.7, z: 0.4572 },
              { x: -0.2, y: 1.2, z: 0.4572 },
            ],
            warnings: [],
          },
          {
            id: "fixture-0007",
            layout_type: "reverse_L",
            orient: "o4",
            module_count: 4,
            shape: "corner",
            asset_key: "l4_reverse_corner",
            asset_fallback_key: null,
            points: [
              { x: 1.3, y: 1.2, z: 0.4572 },
              { x: 0.8, y: 1.2, z: 0.4572 },
              { x: 0.8, y: 0.7, z: 0.4572 },
              { x: 0.3, y: 0.7, z: 0.4572 },
            ],
            warnings: [],
          },
        ],
        fixture_counts_by_layout_type: { L: 2, centerpiece: 1, linear2: 1, linear3: 2, reverse_L: 1 },
        fixture_counts_by_asset_key: {
          centerpiece: 1,
          l4_corner: 1,
          l4_reverse_corner: 1,
          linear2: 1,
          linear3_corner: 1,
          linear3_linear: 1,
          linear4_linear: 1,
        },
        missing_asset_keys: [],
        asset_fallbacks_used: [
          {
            asset_key: "linear3_corner",
            fallback_asset_key: "linear3_linear",
            instance_ids: ["fixture-0004"],
            reason: "optional fixture asset 'linear3_corner' is missing; using 'linear3_linear'.",
          },
        ],
        warnings: ["fixture-0004: optional fixture asset 'linear3_corner' is missing; using 'linear3_linear'."],
      }),
    });
  });
  await page.route(/\/radiance-api\/radiance\/assembly-photometric-layer\?/, async (route) => {
    heatmapMetadataRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: 1,
        mode: "SMD",
        units: "umol/m2/s",
        grid_width: 2,
        grid_height: 2,
        value_count: 4,
        bounds_m: {
          x_min: -1.524,
          x_max: 1.524,
          y_min: -1.524,
          y_max: 1.524,
          z_m: 0.005,
        },
        min_ppfd: 400,
        max_ppfd: 700,
        mean_ppfd: 550,
        target_ppfd: 1000,
        encoding: "float32-le",
        colormap: {
          name: "viridis",
          source: "matplotlib",
          normalization: "linear-clamped",
        },
        color_scale: {
          vmin: 350,
          vmax: 750,
          source: "simulation-visualization-mean-ppfd",
          clamp: true,
        },
        orientation: {
          plane: "xy",
          column_axis: "x",
          row_axis: "y",
          column_order: "ascending",
          row_order: "ascending",
          storage_order: "row-major",
          value_index: "row * grid_width + column",
        },
        warnings: [],
      }),
    });
  });
  await page.route(/\/radiance-api\/radiance\/assembly-photometric-layer\.bin\?/, async (route) => {
    heatmapBinaryRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      body: Buffer.from(new Float32Array([400, 500, 600, 700]).buffer),
    });
  });
  await page.route("**/static/viewer/proposed_led_system/fixture_*.high.glb", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "model/gltf-binary",
      body: tinyGlb(),
    });
  });

  await page.goto("/radiance-simulator");
  const assemblyButton = page.getByRole("button", { name: "View 3D Assembly" });
  await expect(assemblyButton).toBeDisabled();

  await page.getByRole("button", { name: "Run + Visualize" }).click();
  await expect(assemblyButton).toBeEnabled();

  await assemblyButton.click();
  const dialog = page.getByRole("dialog", { name: "3D Assembly" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveClass(/radiance-modal--fullscreen/);
  const frame = page.frameLocator("#modal-frame");
  await expect(frame.locator("#assembly-canvas")).toBeVisible();
  await expect(frame.locator("#assembly-status")).toContainText("3D assembly loaded", { timeout: 20000 });
  await expect(frame.locator("#assembly-mode")).toContainText("Proposed LED System");
  await expect(frame.locator("#assembly-room")).toContainText("3.05 m x 3.05 m");
  await expect(frame.locator("#assembly-count")).toContainText("7 fixture instances");
  await expect(frame.locator("#assembly-dev-panel")).toBeHidden();
  await expect(frame.getByRole("button", { name: /Show Diagnostics/ })).toBeVisible();
  const heatmapToggle = frame.getByRole("checkbox", { name: "PPFD heatmap" });
  const heatmapOpacity = frame.getByLabel("Opacity");
  const fixturesToggle = frame.getByRole("checkbox", { name: "Show fixtures" });
  const plantsToggle = frame.getByRole("checkbox", { name: "Show plants" });
  const fixtureHeight = frame.getByLabel("Fixture height");
  const fixtureHeightValue = frame.locator("#assembly-fixture-height-value");
  const fixtureHeightReset = frame.getByRole("button", { name: "Reset Height" });
  await expect(heatmapToggle).toBeEnabled();
  await expect(heatmapToggle).not.toBeChecked();
  await expect(heatmapOpacity).toBeDisabled();
  await expect(fixturesToggle).toBeEnabled();
  await expect(fixturesToggle).toBeChecked();
  await expect(plantsToggle).toBeEnabled();
  await expect(plantsToggle).toBeChecked();
  await expect(frame.locator("#assembly-plants-status")).toHaveText("1 leaf");
  await expect(fixtureHeight).toBeEnabled();
  await expect(fixtureHeightValue).toContainText("Visual mount: 0.46 m (0.00 m)");
  await expect(fixtureHeightReset).toBeEnabled();
  await expect(frame.locator("#assembly-heatmap-tooltip")).toBeAttached();
  await expect(frame.locator("#assembly-heatmap-status")).toHaveText("Idle");
  expect(heatmapMetadataRequests).toBe(0);
  expect(heatmapBinaryRequests).toBe(0);

  if (!isMobile) {
    await fixturesToggle.uncheck();
    await expect(fixturesToggle).not.toBeChecked();
    await fixturesToggle.check();
    await expect(fixturesToggle).toBeChecked();
    await plantsToggle.uncheck();
    await expect(plantsToggle).not.toBeChecked();
    await plantsToggle.check();
    await expect(plantsToggle).toBeChecked();
    await fixtureHeight.evaluate((input) => {
      input.value = "0.25";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await expect(fixtureHeightValue).toContainText("Visual mount: 0.71 m (+0.25 m)");
    await fixtureHeightReset.click();
    await expect(fixtureHeightValue).toContainText("Visual mount: 0.46 m (0.00 m)");

    await heatmapToggle.check();
    await expect(frame.locator("#assembly-heatmap-status")).toHaveText("On", { timeout: 10000 });
    await expect(heatmapOpacity).toBeEnabled();
    await expect(frame.locator("#assembly-heatmap-tooltip")).toBeHidden();
    expect(heatmapMetadataRequests).toBe(1);
    expect(heatmapBinaryRequests).toBe(1);

    await heatmapToggle.uncheck();
    await expect(frame.locator("#assembly-heatmap-status")).toHaveText("Off");
    await heatmapToggle.check();
    await expect(frame.locator("#assembly-heatmap-status")).toHaveText("On");
    expect(heatmapMetadataRequests).toBe(1);
    expect(heatmapBinaryRequests).toBe(1);

    await frame.getByRole("button", { name: /Show Diagnostics/ }).click();
    await expect(frame.locator("#assembly-dev-panel")).toBeVisible();
    await expect(frame.locator("#assembly-dev-panel")).toContainText("centerpiece");
    await expect(frame.locator("#assembly-dev-panel")).toContainText("linear2");
    await expect(frame.locator("#assembly-dev-panel")).toContainText("linear3_corner");
    await expect(frame.locator("#assembly-dev-panel")).toContainText("linear3_linear");
    await expect(frame.locator("#assembly-dev-panel")).toContainText("linear4_linear");
    await expect(frame.locator("#assembly-dev-panel")).toContainText("l4_corner");
    await expect(frame.locator("#assembly-dev-panel")).toContainText("l4_reverse_corner");
    await expect(frame.locator("#assembly-fit-diagnostics")).toContainText("fixture-0001");
    await expect(frame.locator("#assembly-warnings")).toContainText("Using linear3_linear for missing optional linear3_corner");
    await frame.getByRole("button", { name: /Hide Diagnostics/ }).click();
    await expect(frame.getByRole("button", { name: /Show Diagnostics/ })).toBeVisible();
  }

  const resetCamera = frame.getByRole("button", { name: "Reset Camera" });
  await expect(resetCamera).toBeVisible();
  await resetCamera.press("Enter");
  expect(errors).toEqual([]);
});

[
  {
    mode: "Competitor",
    modeLabel: "Conventional LED System",
    system: "conventional_led_system",
    assetRoot: "conventional_led_system",
  },
  {
    mode: "1000W HPS",
    modeLabel: "1000W HPS System",
    system: "hps_1000w_system",
    assetRoot: "hps_1000w_system",
  },
].forEach((systemCase) => {
  test(`radiance assembly button opens 3D viewer after completed ${systemCase.modeLabel} run`, async ({ page }, testInfo) => {
    const errors = [];
    const isMobile = testInfo.project.name === "mobile";
    page.on("console", (message) => {
      if (message.type() === "error") {
        errors.push(message.text());
      }
    });
    await routeCompletedAssemblyRun(page, directFixtureScene(systemCase));

    await page.goto("/radiance-simulator");
    await page.locator("#rad-mode").selectOption(systemCase.mode);
    const assemblyButton = page.getByRole("button", { name: "View 3D Assembly" });
    await expect(assemblyButton).toBeDisabled();

    await page.getByRole("button", { name: "Run + Visualize" }).click();
    await expect(assemblyButton).toBeEnabled();

    await assemblyButton.click();
    const dialog = page.getByRole("dialog", { name: "3D Assembly" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveClass(/radiance-modal--fullscreen/);
    const frame = page.frameLocator("#modal-frame");
    await expect(frame.locator("#assembly-canvas")).toBeVisible();
    await expect(frame.locator("#assembly-status")).toContainText("3D assembly loaded", { timeout: 20000 });
    await expect(frame.locator("#assembly-mode")).toContainText(systemCase.modeLabel);
    await expect(frame.locator("#assembly-count")).toContainText("1 fixture instances");

    const heatmapToggle = frame.getByRole("checkbox", { name: "PPFD heatmap" });
    const heatmapOpacity = frame.getByLabel("Opacity");
    await expect(heatmapToggle).toBeEnabled();
    await expect(frame.locator("#assembly-heatmap-status")).toHaveText("Idle");
    const fixturesToggle = frame.getByRole("checkbox", { name: "Show fixtures" });
    await expect(frame.locator("#assembly-plants-control")).toBeHidden();
    const fixtureHeight = frame.getByLabel("Fixture height");
    const fixtureHeightValue = frame.locator("#assembly-fixture-height-value");
    const fixtureHeightReset = frame.getByRole("button", { name: "Reset Height" });
    await expect(fixturesToggle).toBeEnabled();
    await expect(fixturesToggle).toBeChecked();
    await expect(fixtureHeight).toBeEnabled();
    await expect(fixtureHeightValue).toContainText("Visual mount: 0.46 m (0.00 m)");
    await expect(fixtureHeightReset).toBeEnabled();

    if (!isMobile) {
      await heatmapToggle.check();
      await expect(frame.locator("#assembly-heatmap-status")).toHaveText("On", { timeout: 10000 });
      await expect(heatmapOpacity).toBeEnabled();

      await fixturesToggle.uncheck();
      await expect(fixturesToggle).not.toBeChecked();
      await expect(frame.locator("#assembly-heatmap-status")).toHaveText("On");
      await fixtureHeight.evaluate((input) => {
        input.value = "0.25";
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await expect(fixtureHeightValue).toContainText("Visual mount: 0.71 m (+0.25 m)");
      await expect(frame.locator("#assembly-heatmap-status")).toHaveText("On");
      await heatmapToggle.uncheck();
      await expect(frame.locator("#assembly-heatmap-status")).toHaveText("Off");
      await fixturesToggle.check();
      await expect(fixturesToggle).toBeChecked();
      await fixtureHeightReset.click();
      await expect(fixtureHeightValue).toContainText("Visual mount: 0.46 m (0.00 m)");
      await heatmapToggle.check();
      await expect(frame.locator("#assembly-heatmap-status")).toHaveText("On");

      await frame.getByRole("button", { name: /Show Diagnostics/ }).click();
      await expect(frame.locator("#assembly-dev-panel")).toBeVisible();
      await expect(frame.locator("#assembly-dev-panel")).toContainText("fixture");
      await expect(frame.locator("#assembly-fit-diagnostics")).toContainText("fixture-0001");
    }

    expect(errors).toEqual([]);
  });
});

test("radiance assembly button disables when completed run becomes stale", async ({ page }) => {
  await routeCompletedAssemblyRun(
    page,
    directFixtureScene({
      mode: "Competitor",
      modeLabel: "Conventional LED System",
      system: "conventional_led_system",
      assetRoot: "conventional_led_system",
    }),
  );

  await page.goto("/radiance-simulator");
  await page.locator("#rad-mode").selectOption("Competitor");
  const assemblyButton = page.getByRole("button", { name: "View 3D Assembly" });
  await expect(assemblyButton).toBeDisabled();

  await page.getByRole("button", { name: "Run + Visualize" }).click();
  await expect(assemblyButton).toBeEnabled();

  await page.locator("#rad-target").fill("900");
  await expect(assemblyButton).toBeDisabled();
});
