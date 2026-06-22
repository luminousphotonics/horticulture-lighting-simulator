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
