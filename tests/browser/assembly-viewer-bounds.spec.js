// @ts-check

const { expect, test } = require("@playwright/test");

test("assembly bounds include edge fixture points beyond room-only extents", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { computeAssemblySceneBounds } = await import("/static/js/assembly-viewer/bounds.js");
    const scenePayload = {
      room: { length_m: 4, width_m: 4, mount_z_m: 0.6 },
      instances: [
        { id: "edge-a", points: [{ x: -3.4, y: -3.2, z: 0.8 }] },
        { id: "edge-b", points: [{ x: 3.6, y: 3.3, z: 0.8 }] },
      ],
    };
    const roomOnly = computeAssemblySceneBounds(null, { room: scenePayload.room, instances: [] });
    const assembly = computeAssemblySceneBounds(null, scenePayload);
    return {
      roomWidth: roomOnly.max.x - roomOnly.min.x,
      roomDepth: roomOnly.max.z - roomOnly.min.z,
      assemblyWidth: assembly.max.x - assembly.min.x,
      assemblyDepth: assembly.max.z - assembly.min.z,
      assemblyTop: assembly.max.y,
    };
  });

  expect(result.assemblyWidth).toBeGreaterThan(result.roomWidth);
  expect(result.assemblyDepth).toBeGreaterThan(result.roomDepth);
  expect(result.assemblyTop).toBeGreaterThan(0.7);
});

test("directional shadow camera is configured from padded assembly bounds", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const THREE = await import("/static/vendor/three/three.module.js");
    const { configureDirectionalLightShadow } = await import("/static/js/assembly-viewer/renderer.js");
    const scene = new THREE.Scene();
    const light = new THREE.DirectionalLight(0xffffff, 1);
    light.position.set(-4, 8, 5);
    scene.add(light);
    scene.add(light.target);
    const bounds = new THREE.Box3(new THREE.Vector3(-5, 0, -4), new THREE.Vector3(6, 2, 7));
    const configured = configureDirectionalLightShadow(light, bounds, { paddingM: 2 });
    return {
      configured,
      width: configured.right - configured.left,
      height: configured.top - configured.bottom,
      depth: configured.far - configured.near,
      target: light.target.position.toArray(),
    };
  });

  expect(result.width).toBeGreaterThan(11);
  expect(result.height).toBeGreaterThan(11);
  expect(result.depth).toBeGreaterThan(1);
  expect(result.configured.near).toBeGreaterThanOrEqual(0.1);
  expect(result.configured.far).toBeGreaterThan(result.configured.near);
  expect(result.target).toEqual([0.5, 1, 1.5]);
});

test("fixture instanced meshes get fresh bounds and disabled frustum culling", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const THREE = await import("/static/vendor/three/three.module.js");
    const { createFixtureInstancing } = await import("/static/js/assembly-viewer/lod.js");
    const sourceRoot = new THREE.Group();
    sourceRoot.add(new THREE.Mesh(new THREE.BoxGeometry(1, 0.2, 1), new THREE.MeshBasicMaterial()));
    const instancing = createFixtureInstancing(
      sourceRoot,
      [
        { id: "left-edge", points: [{ x: -6, y: 0, z: 0.8 }] },
        { id: "right-edge", points: [{ x: 6, y: 0, z: 0.8 }] },
      ],
      { viewerScale: 1 },
    );
    let mesh = null;
    instancing.group.traverse((object) => {
      if (object.isInstancedMesh) {
        mesh = object;
      }
    });
    return {
      frustumCulled: mesh.frustumCulled,
      matrixVersion: mesh.instanceMatrix.version,
      minX: mesh.boundingBox.min.x,
      maxX: mesh.boundingBox.max.x,
      radius: mesh.boundingSphere.radius,
    };
  });

  expect(result.frustumCulled).toBe(false);
  expect(result.matrixVersion).toBeGreaterThan(0);
  expect(result.minX).toBeLessThan(-6.4);
  expect(result.maxX).toBeGreaterThan(6.4);
  expect(result.radius).toBeGreaterThan(6);
});

test("direct fixture placement uses rigid matrices and instanced culling safeguards", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const THREE = await import("/static/vendor/three/three.module.js");
    const { createDirectFixtureInstancing, directFixturePlacementMatrix } = await import("/static/js/assembly-viewer/lod.js");
    const close = (actual, expected) => Math.abs(actual - expected) < 1e-6;

    const centerYawWarnings = [];
    const centerYaw = directFixturePlacementMatrix(
      { id: "center-yaw", position: { x: 2, y: -3, z: 0.5 }, yaw_deg: 90 },
      centerYawWarnings,
    );
    const centerYawPosition = new THREE.Vector3().setFromMatrixPosition(centerYaw.matrix);

    const matrixWarnings = [];
    const matrixPlacement = directFixturePlacementMatrix(
      {
        id: "matrix",
        transform: {
          matrix4: [
            1, 0, 0, 4,
            0, 1, 0, 0.7,
            0, 0, 1, -2,
            0, 0, 0, 1,
          ],
        },
      },
      matrixWarnings,
    );
    const matrixPosition = new THREE.Vector3().setFromMatrixPosition(matrixPlacement.matrix);

    const badWarnings = [];
    const badPlacement = directFixturePlacementMatrix(
      {
        id: "bad",
        position: { x: -1, y: 1.25, z: 0.6 },
        yaw_deg: 0,
        transform: {
          matrix4: [
            1, 0.25, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1,
          ],
        },
      },
      badWarnings,
    );
    const badPosition = new THREE.Vector3().setFromMatrixPosition(badPlacement.matrix);

    const sourceRoot = new THREE.Group();
    sourceRoot.add(new THREE.Mesh(new THREE.BoxGeometry(1, 0.25, 1), new THREE.MeshBasicMaterial()));
    const instancing = createDirectFixtureInstancing(
      sourceRoot,
      [
        { id: "fixture-a", position: { x: -2, y: -1, z: 0.4 }, yaw_deg: 0 },
        { id: "fixture-b", position: { x: 3, y: 1.5, z: 0.8 }, yaw_deg: 45 },
      ],
      { assetKey: "fixture", mountOrientation: "leds_down", viewerScale: 1 },
    );
    let mesh = null;
    instancing.group.traverse((object) => {
      if (object.isInstancedMesh) {
        mesh = object;
      }
    });
    const fixtureBMatrix = new THREE.Matrix4();
    mesh.getMatrixAt(1, fixtureBMatrix);
    const fixtureBPosition = new THREE.Vector3().setFromMatrixPosition(fixtureBMatrix);

    return {
      centerYaw: {
        source: centerYaw.matrixSource,
        position: centerYawPosition.toArray(),
        yawRadians: centerYaw.yawRadians,
        warnings: centerYawWarnings,
      },
      matrix: {
        source: matrixPlacement.matrixSource,
        position: matrixPosition.toArray(),
        warnings: matrixWarnings,
      },
      bad: {
        source: badPlacement.matrixSource,
        position: badPosition.toArray(),
        warnings: badWarnings,
      },
      instancing: {
        instanceCount: instancing.instanceCount,
        meshCount: instancing.meshCount,
        warningCount: instancing.warnings.length,
        diagnosticMode: instancing.diagnostics[0].mode,
        bottomAnchorOffsetM: instancing.diagnostics[0].bottomAnchorOffsetM,
        frustumCulled: mesh.frustumCulled,
        matrixVersion: mesh.instanceMatrix.version,
        fixtureBPosition: fixtureBPosition.toArray(),
        minX: mesh.boundingBox.min.x,
        minY: mesh.boundingBox.min.y,
        maxX: mesh.boundingBox.max.x,
      },
      closeChecks: [
        close(centerYawPosition.x, 2),
        close(centerYawPosition.y, 0.5),
        close(centerYawPosition.z, -3),
        close(matrixPosition.x, 4),
        close(matrixPosition.y, 0.7),
        close(matrixPosition.z, -2),
        close(badPosition.x, -1),
        close(badPosition.y, 0.6),
        close(badPosition.z, 1.25),
        close(fixtureBPosition.x, 3),
        close(fixtureBPosition.y, 0.925),
        close(fixtureBPosition.z, 1.5),
      ],
    };
  });

  expect(result.closeChecks).toEqual(Array(result.closeChecks.length).fill(true));
  expect(result.centerYaw.source).toBe("position_yaw");
  expect(result.centerYaw.yawRadians).toBeCloseTo(Math.PI / 2, 12);
  expect(result.centerYaw.warnings).toEqual([]);
  expect(result.matrix.source).toBe("matrix");
  expect(result.matrix.warnings).toEqual([]);
  expect(result.bad.source).toBe("position_yaw");
  expect(result.bad.warnings[0]).toContain("sheared");
  expect(result.instancing.instanceCount).toBe(2);
  expect(result.instancing.meshCount).toBe(1);
  expect(result.instancing.warningCount).toBe(0);
  expect(result.instancing.diagnosticMode).toBe("direct_fixture");
  expect(result.instancing.bottomAnchorOffsetM).toBeCloseTo(0.125, 12);
  expect(result.instancing.frustumCulled).toBe(false);
  expect(result.instancing.matrixVersion).toBeGreaterThan(0);
  expect(result.instancing.minX).toBeLessThan(-2.4);
  expect(result.instancing.minY).toBeCloseTo(0.4, 6);
  expect(result.instancing.maxX).toBeGreaterThan(3.4);
});

test("proposed LED material tuning is scoped and does not stack", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const THREE = await import("/static/vendor/three/three.module.js");
    const { applySystemMaterialTuning } = await import("/static/js/assembly-viewer/materials.js");
    const sharedMaterial = new THREE.MeshStandardMaterial({
      color: 0x303030,
      emissive: 0x000000,
      metalness: 0.8,
      roughness: 0.9,
    });
    const original = {
      color: sharedMaterial.color.toArray(),
      emissive: sharedMaterial.emissive.toArray(),
      metalness: sharedMaterial.metalness,
      roughness: sharedMaterial.roughness,
    };

    function fixtureRoot() {
      const root = new THREE.Group();
      root.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), sharedMaterial));
      return root;
    }

    const proposedRoot = fixtureRoot();
    const conventionalRoot = fixtureRoot();
    const hpsRoot = fixtureRoot();
    const proposedFirst = applySystemMaterialTuning({ system: "proposed_led_system" }, proposedRoot);
    const proposedMaterial = proposedRoot.children[0].material;
    const afterFirst = {
      color: proposedMaterial.color.toArray(),
      emissive: proposedMaterial.emissive.toArray(),
      metalness: proposedMaterial.metalness,
      roughness: proposedMaterial.roughness,
      marker: proposedMaterial.userData.radRebuildMaterialTuning,
    };
    const proposedSecond = applySystemMaterialTuning({ system: "proposed_led_system" }, proposedRoot);
    const afterSecond = {
      color: proposedMaterial.color.toArray(),
      emissive: proposedMaterial.emissive.toArray(),
      metalness: proposedMaterial.metalness,
      roughness: proposedMaterial.roughness,
      marker: proposedMaterial.userData.radRebuildMaterialTuning,
    };
    const conventional = applySystemMaterialTuning({ system: "conventional_led_system" }, conventionalRoot);
    const hps = applySystemMaterialTuning({ system: "hps_1000w_system" }, hpsRoot);

    return {
      proposedFirst,
      proposedSecond,
      conventional,
      hps,
      proposedWasCloned: proposedMaterial !== sharedMaterial,
      conventionalShared: conventionalRoot.children[0].material === sharedMaterial,
      hpsShared: hpsRoot.children[0].material === sharedMaterial,
      original,
      shared: {
        color: sharedMaterial.color.toArray(),
        emissive: sharedMaterial.emissive.toArray(),
        metalness: sharedMaterial.metalness,
        roughness: sharedMaterial.roughness,
      },
      afterFirst,
      afterSecond,
    };
  });

  expect(result.proposedFirst.applied).toBe(true);
  expect(result.proposedSecond.applied).toBe(true);
  expect(result.conventional.applied).toBe(false);
  expect(result.hps.applied).toBe(false);
  expect(result.proposedWasCloned).toBe(true);
  expect(result.conventionalShared).toBe(true);
  expect(result.hpsShared).toBe(true);
  expect(result.shared).toEqual(result.original);
  expect(result.afterFirst.color[0]).toBeGreaterThan(result.original.color[0]);
  expect(result.afterFirst.emissive[0]).toBeGreaterThan(result.original.emissive[0]);
  expect(result.afterFirst.metalness).toBeLessThan(result.original.metalness);
  expect(result.afterFirst.roughness).toBeLessThan(result.original.roughness);
  expect(result.afterSecond).toEqual(result.afterFirst);
});

test("FSPM panel formatter summarizes available plant metrics", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { buildFspmPanelSections, hasFspmPanelData } = await import("/static/js/assembly-viewer/fspm-panel.js");
    const emptyScene = { schema_version: 3, instances: [] };
    const scene = {
      fspm_metrics: {
        counts: {
          plant_count: 2,
          leaf_count: 8,
          surface_count: 16,
          one_sided_leaf_area_m2: 0.124,
        },
        plant_surface_absorption: {
          status: "computed",
          method: "radiance_leaf_surface_receiver_v1",
          target_ppfd_umol_m2_s: 275,
          target_tolerance_umol_m2_s: 20,
          target_classification_basis_label: "canopy-plane equivalent incident PPFD",
          target_classification_source: "interpolated_runtime_ppfd_map",
          target_range_leaf_count: 5,
          under_lit_leaf_count: 2,
          over_lit_leaf_count: 1,
          target_classification_mean_ppfd_umol_m2_s: 274.2,
          target_capped_incident_flux_total_umol_s: 34,
          excess_incident_flux_above_target_umol_s: 4,
          deficit_to_target_incident_flux_umol_s: 7,
          plant_to_plant_target_capped_incident_flux_cv: 0.06,
          raw_leaf_surface_flux_summary: {
            mean: 271.5,
            min: 120,
            p05: 135,
            median: 274,
            p95: 330,
            max: 360,
            mean_percent_of_target: 98.7272727273,
            min_percent_of_target: 43.6363636364,
            p05_percent_of_target: 49.0909090909,
            median_percent_of_target: 99.6363636364,
            p95_percent_of_target: 120,
            max_percent_of_target: 130.9090909091,
            bucket_counts: [
              { label: "0-20%", leaf_count: 0 },
              { label: "20-40%", leaf_count: 0 },
              { label: "40-55%", leaf_count: 2 },
              { label: "55-80%", leaf_count: 0 },
              { label: "80-100%", leaf_count: 4 },
              { label: "100-120%", leaf_count: 1 },
              { label: "120-150%", leaf_count: 1 },
              { label: "150%+", leaf_count: 0 },
            ],
            summary_granularity: "leaf_average",
            visualization_granularity: "mesh_patch",
            units: "umol/m²/s",
          },
          raw_primary_side: "front",
          receiver_side_policy: "front_and_back_per_mesh_surface_row",
          raw_leaf_surface_flux_side_summaries: {
            front: {
              mean: 300,
              min: 200,
              p05: 210,
              median: 300,
              p95: 390,
              max: 400,
              mean_percent_of_target: 109.0909090909,
              min_percent_of_target: 72.7272727273,
              p05_percent_of_target: 76.3636363636,
              median_percent_of_target: 109.0909090909,
              p95_percent_of_target: 141.8181818182,
              max_percent_of_target: 145.4545454545,
              raw_incident_flux_umol_s: 50,
              bucket_counts: [
                { label: "0-20%", sample_count: 0, sample_percent: 0 },
                { label: "20-40%", sample_count: 0, sample_percent: 0 },
                { label: "40-55%", sample_count: 0, sample_percent: 0 },
                { label: "55-80%", sample_count: 1, sample_percent: 25 },
                { label: "80-100%", sample_count: 0, sample_percent: 0 },
                { label: "100-120%", sample_count: 2, sample_percent: 50 },
                { label: "120-150%", sample_count: 1, sample_percent: 25 },
                { label: "150%+", sample_count: 0, sample_percent: 0 },
              ],
            },
            back: {
              mean: 30,
              min: 10,
              p05: 12,
              median: 30,
              p95: 48,
              max: 50,
              mean_percent_of_target: 10.9090909091,
              min_percent_of_target: 3.6363636364,
              p05_percent_of_target: 4.3636363636,
              median_percent_of_target: 10.9090909091,
              p95_percent_of_target: 17.4545454545,
              max_percent_of_target: 18.1818181818,
              raw_incident_flux_umol_s: 10,
              bucket_counts: [
                { label: "0-2%", sample_count: 0, sample_percent: 0 },
                { label: "2-5%", sample_count: 1, sample_percent: 25 },
                { label: "5-10%", sample_count: 0, sample_percent: 0 },
                { label: "10-20%", sample_count: 3, sample_percent: 75 },
                { label: "20-35%", sample_count: 0, sample_percent: 0 },
                { label: "35-50%", sample_count: 0, sample_percent: 0 },
                { label: "50-75%", sample_count: 0, sample_percent: 0 },
                { label: "75%+", sample_count: 0, sample_percent: 0 },
              ],
            },
            two_sided: {
              total_raw_incident_flux_umol_s: 60,
              backside_contribution_percent: 16.6666666667,
            },
          },
          lower_tail_raw_flux_density_umol_m2_s: 150,
          lower_tail_target_classification_ppfd_umol_m2_s: 180,
          target_capped_incident_mean_flux_density_umol_m2_s: 274.2,
          total_absorbed_photon_flux_umol_s: 42,
          total_incident_photon_flux_umol_s: 60,
          mean_absorbed_fraction_of_incident: 0.7,
          mean_absorbed_photon_flux_density_umol_m2_s: 338.71,
          lower_tail_absorbed_photon_flux_density_umol_m2_s: 120,
          plant_to_plant_absorbed_photon_flux_cv: 0.08,
        },
        spectral_exposure: {
          status: "computed",
          method: "surface_flux_band_weighted_leaf_absorptance_v1",
          fspm_spectral_transport_mode: "banded_5",
          total_absorbed_par_photon_flux_umol_s: 35,
          band_totals: {
            blue: { absorbed_photon_flux_umol_s: 8 },
            green: { absorbed_photon_flux_umol_s: 9 },
            red: { absorbed_photon_flux_umol_s: 18 },
            far_red: { absorbed_photon_flux_umol_s: 7 },
          },
        },
        photosynthetic_light_response_potential: {
          calibration_status: "uncalibrated_model_scaffold",
          input_basis: "absorbed_par",
          area_weighted_mean_local_response_0_1: 0.71,
          equal_plant_mean_normalized_response_0_1: 0.69,
          local_response_p10_0_1: 0.44,
          bottom_decile_area_weighted_response_0_1: 0.42,
          nonuniformity_response_retention_0_1: 0.93,
          plant_to_plant_photosynthetic_response_cv: 0.12,
        },
        photoreceptor_exposure: {
          status: "computed",
          method: "spectral_band_exposure_inputs_v1",
          fspm_spectral_transport_mode: "banded_5",
          mean_absorbed_blue_pfd_umol_m2_s: 64,
          mean_absorbed_green_pfd_umol_m2_s: 72,
          mean_absorbed_red_pfd_umol_m2_s: 145,
          mean_absorbed_far_red_pfd_umol_m2_s: 56,
          mean_absorbed_blue_fraction_of_par: 0.23,
          mean_absorbed_red_to_far_red_ratio_diagnostic: 2.59,
          phytochrome_pss_proxy: { value: null, status: "not_computed" },
          blue_photon_dose: { value_umol_m2: null, status: "not_computed" },
        },
        limitations_note: "Lighting-analysis input only; response potentials are unvalidated and are not biological production forecasts.",
      },
    };
    const staleSpectralScene = {
      fspm_metrics: {
        counts: { plant_count: 1, leaf_count: 1 },
        plant_surface_absorption: {
          status: "computed",
          target_ppfd_umol_m2_s: 275,
          target_range_leaf_count: 1,
          raw_leaf_surface_flux_summary: { mean: 275, units: "umol/m²/s" },
        },
        spectral_exposure: {
          status: "computed",
          method: "stale_scalar_payload",
          total_absorbed_par_photon_flux_umol_s: 35,
        },
      },
    };
    const sections = buildFspmPanelSections(scene);
    const staleTitles = buildFspmPanelSections(staleSpectralScene).map((section) => section.title);
    const text = sections.map((section) => [
      section.title,
      section.note || "",
      ...(section.rows || []).flat(),
    ].join(" ")).join(" ");
    return {
      emptyAvailable: hasFspmPanelData(emptyScene),
      available: hasFspmPanelData(scene),
      sectionTitles: sections.map((section) => section.title),
      staleTitles,
      text,
    };
  });

  expect(result.emptyAvailable).toBe(false);
  expect(result.available).toBe(true);
  expect(result.sectionTitles).toContain("Raw leaf-surface flux");
  expect(result.sectionTitles).toContain("Plant-location target coverage");
  expect(result.sectionTitles).toContain("spectral exposure");
  expect(result.sectionTitles).toContain("photoreceptor exposure");
  expect(result.staleTitles).not.toContain("spectral exposure");
  expect(result.text).toContain("Target PPFD");
  expect(result.text).toContain("Top/front mean 300.0 umol/m²/s (109% target)");
  expect(result.text).toContain("Bottom/back mean 30.0 umol/m²/s (11% target)");
  expect(result.text).toContain("Backside contribution 16.7 %");
  expect(result.text).toContain("Primary raw exposure side Top/front");
  expect(result.text).toContain("Visualization granularity mesh patch surface detail");
  expect(result.text).toContain("Actual receiver-based incident PPFD separated by top/front and bottom/back");
  expect(result.text).toContain("Top/front bucket 100-120% 2 samples (50.0%)");
  expect(result.text).toContain("Bottom/back bucket 10-20% 3 samples (75.0%)");
  expect(result.text).toContain("Target-range leaves");
  expect(result.text).toContain("Coverage basis");
  expect(result.text).toContain("Source baseline PPFD map sampled at leaf XY positions");
  expect(result.text).toContain("Mean plant-location reference PPFD");
  expect(result.text).toContain("Plant-to-plant target-capped coverage CV");
  expect(result.text).not.toContain("Receiver role");
  expect(result.text).not.toContain("Target-classification mean PPFD");
  expect(result.text).toContain("5 leaves");
  expect(result.text).toContain("6.0%");
  expect(result.text).not.toContain("Legacy broadband absorbed");
  expect(result.text).not.toContain("undefined");
  expect(result.text).not.toContain("null");
  expect(result.text).not.toMatch(/yield|biomass|harvest|crop output|growth prediction/i);
});

test("FSPM CSV URL is derived from assembly scene identity", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { fspmCsvUrlFromSceneUrl } = await import("/static/js/assembly-viewer/scene-loader.js");
    const sceneUrl = "/radiance-api/radiance/assembly-scene?mode=SMD&match_system_ppe=true&plants_enabled=true&plant_seed=13&fspm_target_ppfd_umol_m2_s=275&fspm_target_tolerance_umol_m2_s=20&artifact_token=abc&session_id=s1";
    return fspmCsvUrlFromSceneUrl(sceneUrl);
  });
  const parsed = new URL(result, "http://example.test");

  expect(parsed.pathname).toBe("/radiance-api/radiance/fspm-csv");
  expect(parsed.searchParams.get("match_system_ppe")).toBe("true");
  expect(parsed.searchParams.get("plants_enabled")).toBe("true");
  expect(parsed.searchParams.get("plant_seed")).toBe("13");
  expect(parsed.searchParams.get("fspm_target_ppfd_umol_m2_s")).toBe("275");
  expect(parsed.searchParams.get("fspm_target_tolerance_umol_m2_s")).toBe("20");
  expect(parsed.searchParams.get("artifact_token")).toBe("abc");
  expect(parsed.searchParams.get("session_id")).toBe("s1");
});

test("plant surface-flux legends are visual and mode-aware", async ({ page }) => {
  await page.route("**/radiance-api/radiance/assembly-scene**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: 3,
        mode: "SMD",
        room: { length_m: 3.048, width_m: 3.048, mount_z_m: 0.4572 },
        instances: [],
        plants: {
          schema: "rad_rebuild.fspm.plants.viewer.v1",
          units: "meters",
          material: { id: "plant_leaf_material", reflectance: 0.22, transmittance: 0.08, absorptance: 0.7 },
          surface_flux: {
            schema: "rad_rebuild.fspm.plant_surface_flux.v1",
            target_ppfd_umol_m2_s: 275,
            target_tolerance_umol_m2_s: 20,
            visualization: {
              color_metric: "incident_photon_flux_density_umol_m2_s",
              raw_leaf_surface_flux_scale: {
                mode: "raw_leaf_surface_flux",
                scale_type: "target_normalized_ratio",
                target_ppfd_umol_m2_s: 275,
                target_source: "fspm_target_ppfd_umol_m2_s",
                ratio_min: 0,
                ratio_max: 1.5,
                clamp_min_ratio: 0,
                clamp_max_ratio: 1.5,
                anchors: [
                  { ratio: 0, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" },
                  { ratio: 0.2, percent: 20, ppfd_umol_m2_s: 55, color: "#06B6D4" },
                  { ratio: 0.4, percent: 40, ppfd_umol_m2_s: 110, color: "#14B8A6" },
                  { ratio: 0.55, percent: 55, ppfd_umol_m2_s: 151.25, color: "#22C55E" },
                  { ratio: 0.8, percent: 80, ppfd_umol_m2_s: 220, color: "#22C55E" },
                  { ratio: 1, percent: 100, ppfd_umol_m2_s: 275, color: "#A3E635" },
                  { ratio: 1.2, percent: 120, ppfd_umol_m2_s: 330, color: "#F59E0B" },
                  { ratio: 1.5, percent: 150, ppfd_umol_m2_s: 412.5, color: "#DC2626" },
                ],
                units: "umol/m²/s",
              },
              raw_leaf_surface_flux_legend: {
                title: "Raw leaf-surface incident PPFD",
                units: "umol/m²/s",
                scale: "% of FSPM target",
                target_ppfd_umol_m2_s: 275,
                anchors: [
                  { ratio: 0, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" },
                  { ratio: 0.2, percent: 20, ppfd_umol_m2_s: 55, color: "#06B6D4" },
                  { ratio: 0.4, percent: 40, ppfd_umol_m2_s: 110, color: "#14B8A6" },
                  { ratio: 0.55, percent: 55, ppfd_umol_m2_s: 151.25, color: "#22C55E" },
                  { ratio: 0.8, percent: 80, ppfd_umol_m2_s: 220, color: "#22C55E" },
                  { ratio: 1, percent: 100, ppfd_umol_m2_s: 275, color: "#A3E635" },
                  { ratio: 1.2, percent: 120, ppfd_umol_m2_s: 330, color: "#F59E0B" },
                  { ratio: 1.5, percent: 150, ppfd_umol_m2_s: 412.5, color: "#DC2626" },
                ],
              },
              raw_leaf_surface_flux_side_scales: {
                front: {
                  target_ppfd_umol_m2_s: 275,
                  units: "umol/m²/s",
                  anchors: [
                    { ratio: 0, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" },
                    { ratio: 0.2, percent: 20, ppfd_umol_m2_s: 55, color: "#06B6D4" },
                    { ratio: 1, percent: 100, ppfd_umol_m2_s: 275, color: "#A3E635" },
                    { ratio: 1.5, percent: 150, ppfd_umol_m2_s: 412.5, color: "#DC2626" },
                  ],
                },
                back: {
                  target_ppfd_umol_m2_s: 275,
                  units: "umol/m²/s",
                  anchors: [
                    { ratio: 0, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" },
                    { ratio: 0.02, percent: 2, ppfd_umol_m2_s: 5.5, color: "#06B6D4" },
                    { ratio: 0.05, percent: 5, ppfd_umol_m2_s: 13.75, color: "#14B8A6" },
                    { ratio: 0.75, percent: 75, ppfd_umol_m2_s: 206.25, color: "#DC2626" },
                  ],
                },
              },
              raw_leaf_surface_flux_side_legends: {
                front: {
                  title: "Top/front raw leaf-surface PPFD",
                  units: "umol/m²/s",
                  scale: "% of FSPM target",
                  target_ppfd_umol_m2_s: 275,
                  note: "Top/front scale = primary exposure comparison",
                  anchors: [
                    { ratio: 0, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" },
                    { ratio: 0.2, percent: 20, ppfd_umol_m2_s: 55, color: "#06B6D4" },
                    { ratio: 1, percent: 100, ppfd_umol_m2_s: 275, color: "#A3E635" },
                    { ratio: 1.5, percent: 150, ppfd_umol_m2_s: 412.5, color: "#DC2626" },
                  ],
                },
                back: {
                  title: "Bottom/back raw leaf-surface PPFD",
                  units: "umol/m²/s",
                  scale: "% of FSPM target",
                  target_ppfd_umol_m2_s: 275,
                  note: "Bottom/back scale = underside/reflected-light diagnostic",
                  anchors: [
                    { ratio: 0, percent: 0, ppfd_umol_m2_s: 0, color: "#2563EB" },
                    { ratio: 0.02, percent: 2, ppfd_umol_m2_s: 5.5, color: "#06B6D4" },
                    { ratio: 0.05, percent: 5, ppfd_umol_m2_s: 13.75, color: "#14B8A6" },
                    { ratio: 0.75, percent: 75, ppfd_umol_m2_s: 206.25, color: "#DC2626" },
                  ],
                },
              },
              leaf_values: [
                {
                  leaf_id: "plant_r000_c000_leaf_000",
                  plant_id: "plant_r000_c000",
                  lighting_region: "target_range",
                  incident_photon_flux_density_umol_m2_s: 275,
                  target_classification_ppfd_umol_m2_s: 275,
                  target_deviation: 0,
                },
              ],
            },
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
                    vertices: [[-0.1, -0.05, 0.02], [0.1, -0.05, 0.03], [0, 0.16, 0.06]],
                    faces: [[0, 1, 2]],
                  },
                },
              ],
            },
          ],
        },
      }),
    });
  });

  const sceneUrl = "/radiance-api/radiance/assembly-scene?mode=SMD&plants_enabled=true";
  await page.goto(`/viewer/assembly?scene=${encodeURIComponent(sceneUrl)}`);
  const legend = page.locator("#assembly-plants-legend");
  await expect(legend).toBeVisible();
  await expect(legend).toContainText("Plant-location target coverage");
  await expect(legend).toContainText("Canopy-reference PPFD fit");
  await expect(legend).toContainText("Target275 umol/m²/s");
  await expect(legend).toContainText("Tolerance20 umol/m²/s");
  await expect(legend.locator(".assembly-viewer__plant-legend-gradient")).toBeVisible();
  await expect(legend.locator(".assembly-viewer__plant-legend-ticks")).toBeVisible();
  await expect(legend).toContainText("-4σ 195");
  await expect(legend).toContainText("-2σ 235");
  await expect(legend).toContainText("-1σ 255");
  await expect(legend).toContainText("0 275");
  await expect(legend).toContainText("+1σ 295");
  await expect(legend).toContainText("+2σ 315");
  await expect(legend).toContainText("+4σ 355");
  await expect(legend).toContainText("+6σ 395");
  await expect(legend.locator(".assembly-viewer__plant-legend-chips")).toHaveCount(0);
  await expect(legend).not.toContainText("Raw leaf-surface incident PPFD · umol/m²/s · % of FSPM target");

  await page.locator("#assembly-plants-color-mode").selectOption("raw_leaf_surface_flux");
  await expect(legend).toContainText("Legend perspective");
  await expect(legend).toContainText("Top/front raw leaf-surface PPFD");
  await expect(legend).toContainText("Top/front scale = primary exposure comparison");
  await expect(legend.locator(".assembly-viewer__plant-legend-gradient")).toBeVisible();
  await expect(legend).toContainText("20% 55");
  await expect(legend).toContainText("100% 275");
  await expect(legend).toContainText("150%+ 413+");
  await expect(legend).not.toContainText("Under-lit");

  await legend.locator("select").selectOption("back");
  await expect(legend).toContainText("Bottom/back raw leaf-surface PPFD");
  await expect(legend).toContainText("Bottom/back scale = underside/reflected-light diagnostic");
  await expect(legend).toContainText("2% 6");
  await expect(legend).toContainText("75%+ 206+");

  await page.locator("#assembly-plants-color-toggle").uncheck();
  await expect(legend).toBeHidden();
});
