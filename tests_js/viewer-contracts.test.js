import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import {
  D3_VIEWER_RESOURCE_VERSION,
  D4_VIEWER_RESOURCE_VERSION,
  LEGACY_SAMPLING_PROFILE,
  LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION,
  MOUNTING_VIEWER_RESOURCE_VERSION,
  OPTIMIZED_SAMPLING_PROFILE,
  PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
  PROFILE_ID,
  VIEWER_RESOURCE_VERSION,
  parseInstanceTranslations,
  parseScientificGlb,
  validateProfileManifest,
  validateRunScene,
  validateSurfaceFluxSceneReference,
  validateViewerGeneration,
  verifyHash,
} from "../src/fspm_optics/resources/viewer/artifacts.js";
import {
  COLORMAP_ID,
  DEFAULT_OPACITY,
  FRAGMENT_SHADER,
  INTERPOLATION_POLICY_ID,
  VIRIDIS_ANCHOR_COUNT,
  createPpfdHeatmapResources,
  normalizePpfdForDisplay,
  parsePpfdScatter,
  ppfdSrgbAtDisplayScale,
  sampleBilinearScalarGrid,
  validatePpfdHeatmapMetadata,
  validatePpfdHeatmapSceneReference,
  viridisLegendCssGradient,
  viridisSrgbAtNormalized,
} from "../src/fspm_optics/resources/viewer/ppfd-heatmap.js";
import {
  TARGET_CLASSIFICATION_BASIS,
  TARGET_CLASSIFICATION_SOURCE,
  TARGET_COVERAGE_LIMITATION,
  TARGET_COVERAGE_PALETTE,
  createTargetCoverageColorController,
  resolveTargetCoverageSamplePosition,
  sampleTargetCoverageAtLeaf,
  targetCoverageColorAtDeviation,
  validateTargetCoverageSceneReference,
} from "../src/fspm_optics/resources/viewer/target-coverage.js";
import {
  APPROVED_FIXTURE_ASSETS,
  combineAuthoritativeBounds,
  parseFixtureMatrices,
  validateEmbeddedFixtureGlb,
  validateFixtureCatalog,
} from "../src/fspm_optics/resources/viewer/fixture-artifacts.js";
import {
  resolveSurfaceIdentity,
  validateGeometryIdentity,
  validateIdentityMap,
} from "../src/fspm_optics/resources/viewer/identity.js";
import { parseReceiverBuffer } from "../src/fspm_optics/resources/viewer/receivers.js";
import {
  bakeFixturePrimitiveToAssetRoot,
  addAlignmentLattice,
  cloneFixturePrimitiveGeometry,
  composeReflectedFixtureInstanceMatrix,
  createReflectedFixtureRootGeometry,
  fixtureMatrixParity,
} from "../src/fspm_optics/resources/viewer/fixture-renderer.js";
import {
  LEAF_MATERIAL_POLICY,
  applyInstanceTranslations,
  createInspectionLightRig,
  createPlantSurface,
  setLeafScientificOverlayVisible,
} from "../src/fspm_optics/resources/viewer/renderer.js";

globalThis.crypto ??= webcrypto;

test("run scene accepts decimal rectangular rooms and exact identities", () => {
  const value = validateRunScene(scene());
  assert.equal(value.schema_version, 4);
  assert.equal(value.viewer_resource_version, VIEWER_RESOURCE_VERSION);
  assert.deepEqual(value.mounting_height, mountingHeight());
  assert.equal(value.profile.sampling_profile_id, OPTIMIZED_SAMPLING_PROFILE);
  assert.deepEqual(value.run, {
    run_id: "a".repeat(32), system_id: "conventional",
  });
  assert.deepEqual(value.requested_room, {
    length_ft: 12.5, length_m: 3.81,
    width_ft: 7.75, width_m: 2.3622,
  });
  assert.equal(value.natural_fit.plant_count, 6);
  assert.equal(value.plant_instances.plant_ids.length, 6);
  const hpsValue = scene();
  hpsValue.mounting_height = mountingHeight(24);
  assert.equal(validateRunScene(hpsValue), hpsValue);
  assert.equal(validateProfileManifest(profile()).profile_id, PROFILE_ID);
  assert.equal(validateProfileManifest(legacyProfile()).schema_version, 1);
  const missingSampling = scene();
  delete missingSampling.profile.sampling_profile_id;
  assert.throws(() => validateRunScene(missingSampling), /sampling profile/);
  assert.throws(
    () => validateRunScene({ ...scene(), run: { ...scene().run, run_id: "wrong" } }),
    /run identity/,
  );
});

test("viewer run information authenticates the Proposed control mode", () => {
  const proposed = scene();
  proposed.run = {
    run_id: "a".repeat(32),
    system_id: "proposed",
    information: {
      proposed_control: {
        mode: "uniform_module_dimming",
        label: "Uniform module dimming",
        basis_matrix_solver_enabled: false,
      },
      proposed_layout: {
        ring_mode: "reduced_one_ring",
        module_pattern_id: "centered_square_reduced_one_ring_v1",
      },
    },
  };
  assert.equal(
    validateRunScene(proposed).run.information.proposed_control.mode,
    "uniform_module_dimming",
  );
  assert.equal(
    validateRunScene(proposed).run.information.proposed_layout.ring_mode,
    "reduced_one_ring",
  );
  const wrongPattern = structuredClone(proposed);
  wrongPattern.run.information.proposed_layout.module_pattern_id =
    "centered_square_full_v1";
  assert.throws(
    () => validateRunScene(wrongPattern),
    /Proposed ring information/,
  );
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url),
    "utf8",
  );
  assert.match(main, /proposedRingModeLabel/);
  assert.match(main, /reduced_one_ring: "Reduced by one ring"/);

  const nonProposed = scene();
  nonProposed.run.information = proposed.run.information;
  assert.throws(
    () => validateRunScene(nonProposed),
    /Proposed control information/,
  );
});

test("viewer authenticates and displays only the active centered COB LES", () => {
  const proposed = scene();
  proposed.run = {
    run_id: "a".repeat(32),
    system_id: "proposed",
    information: {
      proposed_control: {
        mode: "uniform_module_dimming",
        label: "Uniform module dimming",
        basis_matrix_solver_enabled: false,
      },
      proposed_source: {
        source_mode: "cob_source_shape_surrogate",
        classification: "cob_source_shape_surrogate",
        emitter_geometry: {
          shape: "centered_circular_les",
          diameter_m: 0.022,
          centered_on_existing_module_axis: true,
        },
        authenticated_angular_law: {
          input: { sha256: "a".repeat(64) },
          normalization: { identity_sha256: "b".repeat(64) },
        },
        completed_aperture_characterization: {
          traced_ppfd_post_scale: false,
        },
        viewer_active_emitter: {
          representation: "active_centered_internal_cob_les",
          smd_emitter_plane_active: false,
          diameter_m: 0.022,
          fixture_body_transforms_changed: false,
          fixture_occlusion_classification_changed: false,
          modules: [
            {
              module_index: 0,
              center_scientific_xyz_m: [0, 0, 0.4732],
            },
          ],
        },
      },
    },
  };
  assert.equal(
    validateRunScene(proposed).run.information.proposed_source.source_mode,
    "cob_source_shape_surrogate",
  );
  proposed.run.information.proposed_source.viewer_active_emitter
    .smd_emitter_plane_active = true;
  assert.throws(() => validateRunScene(proposed), /COB source information/);
});

// Tier: subsystem.
test("run scene validates requested and aligned portrait room contracts separately", () => {
  for (const systemId of ["proposed", "conventional", "hps"]) {
    const portrait = sceneForRoom(10, 20, systemId);
    assert.equal(validateRunScene(portrait), portrait);
    assert.deepEqual(portrait.requested_room, {
      length_ft: 10, length_m: 3.048, width_ft: 20, width_m: 6.096,
    });
    assert.deepEqual(portrait.room_bounds, {
      minimum_xz: [-3.048, -1.524], maximum_xz: [3.048, 1.524],
    });
    assert.equal(
      portrait.aligned_simulation_room.coordinate_frame.rotation_degrees_about_z,
      -90,
    );
  }

  assert.equal(validateRunScene(sceneForRoom(20, 10, "proposed")).run.system_id,
    "proposed");
  assert.equal(validateRunScene(sceneForRoom(10, 10, "proposed")).run.system_id,
    "proposed");
  assert.equal(validateRunScene(sceneForRoom(30, 50, "proposed")).run.system_id,
    "proposed");

  const wrongAlignedBounds = sceneForRoom(10, 20, "proposed");
  wrongAlignedBounds.room_bounds.maximum_xz[0] = 1.524;
  assert.throws(
    () => validateRunScene(wrongAlignedBounds),
    /room bounds do not match the aligned simulation room/,
  );

  const wrongRequestedRoom = sceneForRoom(10, 20, "proposed");
  wrongRequestedRoom.requested_room.length_ft = 11;
  assert.throws(
    () => validateRunScene(wrongRequestedRoom),
    /requested room is incompatible/,
  );

  const wrongFrame = sceneForRoom(10, 20, "proposed");
  wrongFrame.aligned_simulation_room.coordinate_frame.rotation_degrees_about_z = 90;
  assert.throws(
    () => validateRunScene(wrongFrame),
    /aligned simulation room is incompatible/,
  );
});

// Tier: subsystem.
test("run scene keeps reversed precomputed requests in canonical presentation", () => {
  const canonical = sceneForRoom(30, 15, "conventional");
  const frame = canonical.aligned_simulation_room.coordinate_frame;
  canonical.requested_orientation = {
    schema_id: "fspm-optics.requested-orientation-playback",
    schema_version: 2,
    canonical_case_id: "conventional-practical-30x15-aisle-off",
    canonical_bundle_identity_sha256: "1".repeat(64),
    presentation_identity_sha256: "2".repeat(64),
    coordinate_frame: frame,
    requested_room_ft: {length: 15, width: 30},
    canonical_display_room_ft: {length: 30, width: 15},
    simulation_to_requested_rotation_degrees_about_z: 0,
    viewer_global_rotation_degrees_about_y: 0,
    heatmap_counterclockwise_quarter_turns: 0,
    scalar_metrics_invariant: true,
    fspm_target_tolerance_umol_m2_s: 75,
  };

  assert.equal(validateRunScene(canonical), canonical);
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(main, /presentationRoot/);
  assert.doesNotMatch(main, /viewer_global_rotation_degrees_about_y \|\| 0/);
  canonical.requested_orientation.viewer_global_rotation_degrees_about_y = 90;
  assert.throws(
    () => validateRunScene(canonical),
    /Requested-orientation contract is incompatible/,
  );
});

// Tier: subsystem.
test("run scene accepts authenticated FSPM tolerance presentations", () => {
  const canonical = sceneForRoom(30, 15, "hps");
  const frame = canonical.aligned_simulation_room.coordinate_frame;
  canonical.requested_orientation = {
    schema_id: "fspm-optics.requested-orientation-playback",
    schema_version: 4,
    canonical_case_id: "hps-30x15-aisle-off",
    canonical_bundle_identity_sha256: "1".repeat(64),
    presentation_identity_sha256: "2".repeat(64),
    coordinate_frame: frame,
    requested_room_ft: {length: 30, width: 15},
    canonical_display_room_ft: {length: 30, width: 15},
    simulation_to_requested_rotation_degrees_about_z: 0,
    viewer_global_rotation_degrees_about_y: 0,
    heatmap_counterclockwise_quarter_turns: 0,
    scalar_metrics_invariant: true,
    fspm_target_tolerance_umol_m2_s: 20,
    fspm_tolerance_presentation: {
      schema_id: "fspm-optics.precomputed-fspm-tolerance-presentation",
      schema_version: 1,
      authenticated_bundle_identity_sha256: "1".repeat(64),
      source_derived_playback_identity_sha256: null,
      fspm_target_tolerance_umol_m2_s: 20,
      classification_metadata_only: true,
      stage_a_transport_recomputed: false,
      derivation_identity_sha256: "3".repeat(64),
    },
  };

  assert.equal(validateRunScene(canonical), canonical);
  canonical.requested_orientation.fspm_tolerance_presentation
    .fspm_target_tolerance_umol_m2_s = 21;
  assert.throws(
    () => validateRunScene(canonical),
    /FSPM tolerance presentation is incompatible/,
  );
});

test("run scene validates a centered fixed 2 ft active domain for decimal rooms", () => {
  const aisle = sceneForRoom(10.1, 20.1, "proposed");
  const room = aisle.requested_room;
  const aligned = aisle.aligned_simulation_room;
  aisle.active_domain = {
    schema_id: "fspm-optics.active-room-domain",
    schema_version: 1,
    policy_id: "centered_fixed_2ft_perimeter_aisle_v1",
    enabled: true,
    outer_requested_m: { length: room.length_m, width: room.width_m },
    outer_aligned_m: {
      length_x: aligned.length_m,
      width_y: aligned.width_m,
    },
    aisle: { width_ft_per_wall: 2, width_m_per_wall: 0.6096 },
    active_requested_m: { length: 1.85928, width: 4.90728 },
    active_aligned_m: { length_x: 4.90728, width_y: 1.85928 },
    active_bounds_aligned_m: {
      min_x: -2.45364,
      max_x: 2.45364,
      min_y: -0.92964,
      max_y: 0.92964,
    },
    areas_m2: {
      outer_room: 3.07848 * 6.12648,
      active_grow: 1.85928 * 4.90728,
    },
    centered: true,
    coordinate_frame: aligned.coordinate_frame,
    identity_sha256: "7".repeat(64),
  };
  assert.equal(validateRunScene(aisle), aisle);

  const shifted = structuredClone(aisle);
  shifted.active_domain.active_bounds_aligned_m.min_x += 0.01;
  assert.throws(() => validateRunScene(shifted), /active domain/);
});

// Tier: subsystem.
test("current scenes bind a complete optional PPFD source without weakening core replay", () => {
  const current = scene();
  assert.equal(validateRunScene(current), current);
  assert.equal(validatePpfdHeatmapSceneReference(current), current.ppfd_heatmap);
  assert.equal(current.ppfd_heatmap.interpolation_policy_id, INTERPOLATION_POLICY_ID);
  assert.equal(current.ppfd_heatmap.display_scale.colormap, COLORMAP_ID);
  const thirtyByFifty = scene();
  const xCenters = Array.from({ length: 61 }, (_, index) => (index - 30) * 0.25);
  const yCenters = Array.from({ length: 37 }, (_, index) => (index - 18) * 0.25);
  thirtyByFifty.ppfd_heatmap.grid = {
    ...thirtyByFifty.ppfd_heatmap.grid,
    width: xCenters.length,
    height: yCenters.length,
    x_centers_m: xCenters,
    y_centers_m: yCenters,
    cell_edge_bounds_m: {
      x_min: -7.625, x_max: 7.625, y_min: -4.625, y_max: 4.625,
    },
  };
  thirtyByFifty.ppfd_heatmap.scalar_field.count =
    xCenters.length * yCenters.length;
  thirtyByFifty.ppfd_heatmap.scalar_field.byte_length =
    thirtyByFifty.ppfd_heatmap.scalar_field.count * 12;
  assert.equal(
    validatePpfdHeatmapSceneReference(thirtyByFifty),
    thirtyByFifty.ppfd_heatmap,
  );
  const extraScalarField = scene();
  extraScalarField.ppfd_heatmap.scalar_field.untrusted = true;
  assert.throws(
    () => validatePpfdHeatmapSceneReference(extraScalarField),
    /artifact record/,
  );
  const mismatchedScalar = scene();
  mismatchedScalar.ppfd_heatmap.scalar_field.count = 5;
  mismatchedScalar.ppfd_heatmap.scalar_field.byte_length = 60;
  assert.throws(
    () => validatePpfdHeatmapSceneReference(mismatchedScalar),
    /grid contract/,
  );

  const mountingHistorical = mountingHistoricalScene();
  assert.equal(validateRunScene(mountingHistorical), mountingHistorical);
  assert.equal("ppfd_heatmap" in mountingHistorical, false);
  const heatmapHistorical = scene();
  heatmapHistorical.viewer_resource_version = PPFD_HEATMAP_VIEWER_RESOURCE_VERSION;
  delete heatmapHistorical.ppfd_heatmap.target_coverage;
  assert.equal(validateRunScene(heatmapHistorical), heatmapHistorical);
  assert.equal(validatePpfdHeatmapSceneReference(heatmapHistorical),
    heatmapHistorical.ppfd_heatmap);

  const malformedOptional = scene();
  malformedOptional.viewer_resource_version = PPFD_HEATMAP_VIEWER_RESOURCE_VERSION;
  malformedOptional.ppfd_heatmap = { availability: "available" };
  assert.equal(validateRunScene(malformedOptional), malformedOptional);
  assert.throws(
    () => validatePpfdHeatmapSceneReference(malformedOptional),
    /field inventory/,
  );
});

// Tier: subsystem.
test("Stage A PPFD metadata and little-endian scalar grid reconstruct exactly", () => {
  const current = scene();
  const source = validatePpfdHeatmapSceneReference(current);
  const metadata = ppfdHeatmapMetadata(current, source);
  assert.equal(validatePpfdHeatmapMetadata(metadata, current, source), metadata);
  const transformed = structuredClone(metadata);
  transformed.transforms.smoothing = true;
  assert.throws(
    () => validatePpfdHeatmapMetadata(transformed, current, source),
    /disagrees/,
  );

  const grid = parsePpfdScatter(ppfdScatterBuffer(), source);
  assert.deepEqual([...grid.values], [10, 20, 30, 40]);
  assert.deepEqual(grid.bounds, {
    x_min: -2, x_max: 2, y_min: -4, y_max: 4,
  });
  assert.throws(
    () => parsePpfdScatter(ppfdScatterBuffer([
      [-1, -2, 10], [1, -2, 20], [-1, 2, 30], [-1, -2, 10],
    ]), source),
    /duplicate/,
  );
  assert.throws(
    () => parsePpfdScatter(ppfdScatterBuffer([
      [-1, -2, -1], [1, -2, 20], [-1, 2, 30], [1, 2, 40],
    ]), source),
    /invalid record/,
  );
  assert.throws(
    () => parsePpfdScatter(ppfdScatterBuffer([
      [-1, -2, Number.NaN], [1, -2, 20], [-1, 2, 30], [1, 2, 40],
    ]), source),
    /invalid record/,
  );
  assert.throws(
    () => parsePpfdScatter(ppfdScatterBuffer(undefined, false), source),
    /invalid record/,
  );
});

// Tier: subsystem.
test("CPU tooltip interpolation matches the scalar-first edge-clamped shader policy", () => {
  const source = ppfdHeatmapSource();
  const grid = parsePpfdScatter(ppfdScatterBuffer(), source);
  assert.equal(sampleBilinearScalarGrid(grid, -1, -2), 10);
  assert.equal(sampleBilinearScalarGrid(grid, 1, 2), 40);
  assert.equal(sampleBilinearScalarGrid(grid, 0, 0), 25);
  assert.equal(sampleBilinearScalarGrid(grid, 0, -2), 15);
  assert.equal(sampleBilinearScalarGrid(grid, -100, -100), 10);
  assert.equal(sampleBilinearScalarGrid(grid, 100, 100), 40);
  assert.ok(FRAGMENT_SHADER.indexOf("interpolatedScalar")
    < FRAGMENT_SHADER.indexOf("float normalized"));
  assert.ok(FRAGMENT_SHADER.indexOf("float normalized")
    < FRAGMENT_SHADER.indexOf("vec3 viridis"));
  assert.match(FRAGMENT_SHADER, /scalarUv \* scalarDimensions - vec2\(0\.5\)/);
});

// Tier: subsystem.
test("PPFD colors follow canonical Viridis anchors and the fixed display scale", () => {
  assert.equal(VIRIDIS_ANCHOR_COUNT, 8);
  assert.deepEqual(
    [0, 0.25, 0.5, 0.75, 1].map(viridisSrgbAtNormalized),
    [
      [68, 1, 84],
      [58, 84, 137],
      [35, 145, 139],
      [104, 200, 99],
      [253, 231, 37],
    ],
  );
  const displayScale = {
    minimum_ppfd_umol_m2_s: 800,
    maximum_ppfd_umol_m2_s: 1200,
  };
  assert.deepEqual(
    [800, 900, 1000, 1100, 1200].map(
      (value) => normalizePpfdForDisplay(value, displayScale),
    ),
    [0, 0.25, 0.5, 0.75, 1],
  );
  assert.equal(normalizePpfdForDisplay(0, displayScale), 0);
  assert.equal(normalizePpfdForDisplay(2000, displayScale), 1);
  assert.throws(
    () => normalizePpfdForDisplay(1000, {
      minimum_ppfd_umol_m2_s: 1000,
      maximum_ppfd_umol_m2_s: 1000,
    }),
    /normalization inputs/,
  );
});

// Tier: subsystem.
test("heatmap performs one sRGB round trip and bypasses lighting and tone mapping", () => {
  const source = ppfdHeatmapSource();
  const grid = parsePpfdScatter(ppfdScatterBuffer(), source);
  const resources = createPpfdHeatmapResources({
    validated: { grid, source },
    renderer: { capabilities: { maxTextureSize: 37 } },
  });
  assert.equal(resources.material.isShaderMaterial, true);
  assert.equal(resources.material.lights, false);
  assert.equal(resources.material.toneMapped, false);
  assert.equal(resources.viridisTexture.colorSpace, THREE.SRGBColorSpace);
  assert.equal(
    (FRAGMENT_SHADER.match(/#include <colorspace_fragment>/g) ?? []).length,
    1,
  );
  assert.doesNotMatch(FRAGMENT_SHADER, /tonemapping_fragment|pow\s*\(/);
  assert.match(FRAGMENT_SHADER, /normalized \* 7\.0 \+ 0\.5/);
  assert.match(FRAGMENT_SHADER, /\/ 8\.0/);
  for (let index = 0; index < VIRIDIS_ANCHOR_COUNT; index += 1) {
    assert.deepEqual(
      viridisSrgbAtNormalized(index / (VIRIDIS_ANCHOR_COUNT - 1)),
      Array.from(
        resources.viridisTexture.image.data.slice(index * 4, index * 4 + 3),
      ),
    );
  }
  resources.dispose();
});

// Tier: subsystem.
test("legend and surface share Viridis normalization and final display colors", () => {
  const displayScale = ppfdHeatmapSource().display_scale;
  const values = [750, 800, 900, 1000, 1100, 1200, 1250];
  for (const value of values) {
    assert.deepEqual(
      ppfdSrgbAtDisplayScale(value, displayScale),
      viridisSrgbAtNormalized(normalizePpfdForDisplay(value, displayScale)),
    );
  }
  const gradient = viridisLegendCssGradient();
  assert.match(gradient, /^linear-gradient\(90deg, /);
  assert.match(gradient, /rgb\(68 1 84\) 0%/);
  assert.match(gradient, /rgb\(35 145 139\) 50%/);
  assert.match(gradient, /rgb\(253 231 37\) 100%\)$/);
});

// Tier: subsystem.
test("Proposed and Conventional PPFD fixtures retain data and fixed-scale behavior", () => {
  const displayScale = {
    minimum_ppfd_umol_m2_s: 800,
    maximum_ppfd_umol_m2_s: 1200,
    colormap: COLORMAP_ID,
  };
  const fixtures = [
    {
      systemId: "proposed",
      values: [999.4, 1000.1, 999.8, 1000.5],
      maximumUniqueColors: 2,
    },
    {
      systemId: "conventional",
      values: [800, 920, 1080, 1200],
      minimumUniqueColors: 4,
    },
  ];
  const unchangedMetrics = Object.freeze({
    mean_ppfd_umol_m2_s: 1000,
    coefficient_of_variation: 0.03125,
  });
  for (const fixture of fixtures) {
    const current = sceneForRoom(12, 12, fixture.systemId);
    current.ppfd_heatmap.display_scale = { ...displayScale };
    const source = validatePpfdHeatmapSceneReference(current);
    const scalarBytes = ppfdScatterBuffer([
      [-1, -2, fixture.values[0]],
      [1, -2, fixture.values[1]],
      [-1, 2, fixture.values[2]],
      [1, 2, fixture.values[3]],
    ]);
    const bytesBefore = new Uint8Array(scalarBytes.slice(0));
    const sourceBefore = structuredClone(source);
    const grid = parsePpfdScatter(scalarBytes, source);
    const valuesBefore = Array.from(grid.values);
    const resources = createPpfdHeatmapResources({
      validated: { grid, source },
      renderer: { capabilities: { maxTextureSize: 37 } },
    });
    const colors = new Set(
      fixture.values.map(
        (value) => ppfdSrgbAtDisplayScale(value, displayScale).join(","),
      ),
    );
    if (fixture.maximumUniqueColors !== undefined) {
      assert.ok(colors.size <= fixture.maximumUniqueColors);
    }
    if (fixture.minimumUniqueColors !== undefined) {
      assert.ok(colors.size >= fixture.minimumUniqueColors);
    }
    assert.deepEqual(Array.from(grid.values), valuesBefore);
    assert.deepEqual(new Uint8Array(scalarBytes), bytesBefore);
    assert.deepEqual(source, sourceBefore);
    assert.deepEqual(unchangedMetrics, {
      mean_ppfd_umol_m2_s: 1000,
      coefficient_of_variation: 0.03125,
    });
    assert.deepEqual(
      resources.material.uniforms.displayScale.value.toArray(),
      [800, 1200],
    );
    resources.dispose();
  }
});

// Tier: subsystem.
test("deterministic heatmap appearance regression matches the reviewed SVG", () => {
  const expected = readFileSync(
    new URL("fixtures/ppfd-heatmap-color-regression.svg", import.meta.url),
    "utf8",
  );
  assert.equal(renderPpfdColorRegressionSvg(), expected);
});

// Tier: subsystem.
test("Target Coverage authenticates provenance, policy, palette, support, and failures", () => {
  const current = scene();
  const source = validatePpfdHeatmapSceneReference(current);
  const metadata = ppfdHeatmapMetadata(current, source);
  const contract = validateTargetCoverageSceneReference(current, metadata);
  assert.equal(contract.target_classification_basis, TARGET_CLASSIFICATION_BASIS);
  assert.equal(contract.target_classification_source, TARGET_CLASSIFICATION_SOURCE);
  assert.equal(contract.scientific_limitation, TARGET_COVERAGE_LIMITATION);
  assert.equal(contract.reference.source, "requested_lighting_target");
  assert.equal(contract.tolerance_ppfd_umol_m2_s, 20);
  assert.deepEqual(contract.palette.anchors, TARGET_COVERAGE_PALETTE);
  assert.equal("surface_flux" in current, false);

  const zeroTarget = scene();
  zeroTarget.ppfd_heatmap.target_coverage.reference.ppfd_umol_m2_s = 0;
  assert.equal(
    validateTargetCoverageSceneReference(zeroTarget).reference.ppfd_umol_m2_s,
    0,
  );
  const zeroHps = scene();
  zeroHps.run.system_id = "hps";
  zeroHps.ppfd_heatmap.target_coverage.system_id = "hps";
  zeroHps.ppfd_heatmap.target_coverage.reference = {
    ppfd_umol_m2_s: 0,
    source: "achieved_stage_a_baseline_mean",
    policy_mode: "automatic",
  };
  assert.throws(
    () => validateTargetCoverageSceneReference(zeroHps),
    /reference policy/,
  );
  const adjustedMetadata = ppfdHeatmapMetadata(current, source);
  adjustedMetadata.transforms.target_rescaling = true;
  assert.equal(
    validatePpfdHeatmapMetadata(adjustedMetadata, current, source).transforms
      .target_rescaling,
    true,
  );

  const wrongParent = scene();
  wrongParent.ppfd_heatmap.target_coverage.parent_source_field_identity_sha256 =
    "0".repeat(64);
  assert.throws(() => validateTargetCoverageSceneReference(wrongParent), /identity/);
  const wrongPalette = scene();
  wrongPalette.ppfd_heatmap.target_coverage.palette.anchors[4].srgb_hex = "#000000";
  assert.throws(() => validateTargetCoverageSceneReference(wrongPalette), /palette/);
  const wrongSupport = scene();
  wrongSupport.ppfd_heatmap.target_coverage.sampling.support_bounds_m.x_max = 1;
  assert.throws(() => validateTargetCoverageSceneReference(wrongSupport), /interpolation/);
  const wrongAutomaticPolicy = scene();
  wrongAutomaticPolicy.ppfd_heatmap.target_coverage.reference.source =
    "achieved_stage_a_baseline_mean";
  assert.throws(
    () => validateTargetCoverageSceneReference(wrongAutomaticPolicy), /reference policy/,
  );
  const wrongAxesMetadata = structuredClone(metadata);
  wrongAxesMetadata.orientation.layout_axes_swapped_from_requested_room = true;
  assert.throws(
    () => validateTargetCoverageSceneReference(current, wrongAxesMetadata),
    /interpolation/,
  );
});

// Tier: subsystem.
test("Target Coverage uses authenticated XY mapping, bilinear values, and fixed boundaries", () => {
  const current = scene();
  const contract = validateTargetCoverageSceneReference(current);
  const source = validatePpfdHeatmapSceneReference(current);
  const grid = parsePpfdScatter(ppfdScatterBuffer(), source);
  const center = sampleTargetCoverageAtLeaf(grid, contract, [0, 0, 0], 0);
  assert.deepEqual(center.position, {
    requestedX: 0, requestedY: 0, simulationX: 0, simulationY: 0,
    fieldX: 0, fieldY: 0, insideSupport: true,
  });
  assert.equal(center.coveragePpfd, 25);
  assert.equal(center.deviation, (25 - 1000) / 20);
  const swapped = structuredClone(contract);
  swapped.sampling.axes_swapped_from_requested_room = true;
  const position = resolveTargetCoverageSamplePosition(swapped, [0.25, 0, -0.5], 0);
  assert.deepEqual(position, {
    requestedX: -0.5, requestedY: 0.25,
    simulationX: 0.25, simulationY: 0.5,
    fieldX: 0.25, fieldY: 0.5, insideSupport: true,
  });
  assert.deepEqual(targetCoverageColorAtDeviation(-4), [37, 99, 235]);
  assert.deepEqual(targetCoverageColorAtDeviation(-1), [34, 197, 94]);
  assert.deepEqual(targetCoverageColorAtDeviation(0), [34, 197, 94]);
  assert.deepEqual(targetCoverageColorAtDeviation(1), [34, 197, 94]);
  assert.deepEqual(targetCoverageColorAtDeviation(6), [220, 38, 38]);
  assert.deepEqual(targetCoverageColorAtDeviation(-100), [37, 99, 235]);
  assert.deepEqual(targetCoverageColorAtDeviation(100), [220, 38, 38]);
});

// Tier: subsystem.
test("Target Coverage shares the authenticated R32F texture and restores shader ownership", () => {
  const current = scene();
  const source = validatePpfdHeatmapSceneReference(current);
  const grid = parsePpfdScatter(ppfdScatterBuffer(), source);
  const resources = createPpfdHeatmapResources({
    validated: { grid, source }, renderer: { capabilities: { maxTextureSize: 37 } },
  });
  const attributes = {
    POSITION: new Float32Array(9), NORMAL: new Float32Array(9),
    TEXCOORD_0: new Float32Array([0, 0.5, 0.5, 0, 1, 0.5]),
    _LEAF_INDEX: new Uint32Array(3), _FACE_INDEX: new Uint32Array(3),
    _PATCH_INDEX: new Uint32Array(3),
  };
  const surface = createPlantSurface({ attributes }, 2);
  const translations = { count: 2, values: new Float32Array(6) };
  applyInstanceTranslations(surface, translations);
  const leafCompile = surface.material.onBeforeCompile;
  const controller = createTargetCoverageColorController({
    surface,
    resources,
    contract: validateTargetCoverageSceneReference(current),
    translations,
  });
  const shader = leafShaderStub();
  surface.material.onBeforeCompile(shader, {});
  assert.equal(shader.uniforms.targetCoverageScalarMap.value, resources.scalarTexture);
  assert.match(shader.vertexShader, /manual|targetCoverageScalarAt/);
  assert.match(shader.vertexShader, /instanceMatrix\[3\]\[0\]/);
  assert.doesNotMatch(shader.vertexShader, /targetCoverageAxesSwapped/);
  assert.match(shader.vertexShader, /targetCoverageSimulationXY/);
  assert.match(shader.fragmentShader, /vTargetCoverageValid/);
  assert.match(shader.fragmentShader, /leafFluxColoredSide = true/);
  const replacement = createPpfdHeatmapResources({
    validated: { grid, source }, renderer: { capabilities: { maxTextureSize: 37 } },
  });
  controller.setScalarResources(replacement);
  assert.equal(shader.uniforms.targetCoverageScalarMap.value, replacement.scalarTexture);
  resources.dispose();
  assert.equal(replacement.scalarTexture.image.data, grid.values);
  controller.setEnabled(false);
  assert.equal(controller.getState().enabled, false);
  controller.dispose();
  assert.equal(surface.material.onBeforeCompile, leafCompile);
  replacement.dispose();
  surface.userData.leafMaterialController.dispose();
  surface.geometry.dispose();
  surface.material.dispose();
});

// Tier: subsystem.
test("Target Coverage is event-driven and independent of heatmap visibility and D5 data", () => {
  const target = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/target-coverage.js", import.meta.url),
    "utf8",
  );
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  assert.doesNotMatch(target, /requestAnimationFrame|setAnimationLoop|readPixels/);
  assert.doesNotMatch(target, /surface[-_]?flux|calibration|incident_par|absorbed_par/i);
  assert.doesNotMatch(target, /mesh\.visible|toggle\.checked|controls\.ppfdHeatmap/);
  assert.equal((target.match(/new THREE\.DataTexture/g) ?? []).length, 0);
  assert.match(main, /onResourcesAvailable/);
  assert.match(main, /onResourcesUnavailable/);
  assert.match(main, /syncLeafColoring\(\)/);
  assert.doesNotMatch(main, /surfaceMetric|surfaceSides|surfaceSidesControl/);
});

// Tier: subsystem.
test("GPU resources use authenticated cell edges, transform, limits, and one mesh", () => {
  const source = validatePpfdHeatmapSceneReference(scene());
  const grid = parsePpfdScatter(ppfdScatterBuffer(), source);
  const validated = { grid, source };
  assert.throws(
    () => createPpfdHeatmapResources({
      validated,
      renderer: { capabilities: { maxTextureSize: 1 } },
    }),
    /MAX_TEXTURE_SIZE/,
  );
  const resources = createPpfdHeatmapResources({
    validated,
    renderer: { capabilities: { maxTextureSize: 37 } },
  });
  assert.equal(resources.mesh.isMesh, true);
  assert.equal(resources.mesh.geometry.parameters.width, 4);
  assert.equal(resources.mesh.geometry.parameters.height, 8);
  assert.equal(resources.mesh.position.x, 0);
  assert.equal(resources.mesh.position.y, source.grid.reference_plane_z_m);
  assert.equal(resources.mesh.position.z, 0);
  assert.equal(resources.mesh.rotation.x, -Math.PI / 2);
  assert.equal(resources.material.uniforms.displayOpacity.value, DEFAULT_OPACITY);
  assert.equal(resources.material.forceSinglePass, true);
  assert.equal(resources.material.depthWrite, false);
  const parent = new THREE.Group();
  parent.add(resources.mesh);
  resources.dispose();
  assert.equal(parent.children.length, 0);
});

// Tier: subsystem.
test("PPFD GPU layer is one event-driven reusable draw with owned lifecycle and UI", () => {
  const heatmap = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/ppfd-heatmap.js", import.meta.url),
    "utf8",
  );
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/index.html", import.meta.url), "utf8",
  );
  assert.equal(DEFAULT_OPACITY, 0.70);
  assert.equal((heatmap.match(/new THREE\.PlaneGeometry/g) ?? []).length, 1);
  assert.equal((heatmap.match(/new THREE\.ShaderMaterial/g) ?? []).length, 1);
  assert.equal((heatmap.match(/new THREE\.Mesh\(/g) ?? []).length, 1);
  assert.equal((heatmap.match(/new THREE\.DataTexture/g) ?? []).length, 2);
  assert.match(heatmap, /getFloat32\(offset, true\)/);
  const metadataLengthCheck = heatmap.indexOf(
    "validateFetchedLength(metadataBytes",
  );
  const metadataHashCheck = heatmap.indexOf("await verifyHash(", metadataLengthCheck);
  const metadataDecode = heatmap.indexOf("decodeJson(metadataBytes", metadataHashCheck);
  const scalarLengthCheck = heatmap.indexOf("validateFetchedLength(scalarBytes");
  const scalarHashCheck = heatmap.indexOf("await verifyHash(", scalarLengthCheck);
  const scalarDecode = heatmap.indexOf("parsePpfdScatter(scalarBytes", scalarHashCheck);
  assert.ok(metadataLengthCheck < metadataHashCheck && metadataHashCheck < metadataDecode);
  assert.ok(scalarLengthCheck < scalarHashCheck && scalarHashCheck < scalarDecode);
  assert.match(heatmap, /capabilities\?\.maxTextureSize/);
  assert.ok(heatmap.indexOf("capabilities?.maxTextureSize")
    < heatmap.indexOf("new THREE.DataTexture"));
  assert.match(heatmap, /THREE\.NearestFilter/);
  assert.match(heatmap, /mesh\.rotation\.x = -Math\.PI \/ 2/);
  assert.match(heatmap, /-\(bounds\.y_min \+ bounds\.y_max\) \* 0\.5/);
  assert.match(heatmap, /hit\.point\.x,[\s\S]*-hit\.point\.z/);
  assert.match(heatmap, /reference_plane_z_m/);
  assert.match(heatmap, /polygonOffset: true/);
  assert.match(heatmap, /depthWrite: false/);
  assert.match(heatmap, /transparent: true/);
  assert.match(heatmap, /material\.forceSinglePass = true/);
  assert.match(heatmap, /loadAbort\?\.abort\(\)/);
  assert.match(heatmap, /generation !== loadGeneration/);
  assert.match(heatmap, /sceneManifest\.ppfd_heatmap === undefined/);
  for (const state of ["loading", "available", "hidden", "unavailable", "corrupt"]) {
    assert.match(heatmap, new RegExp(`"${state}"`));
  }
  assert.match(heatmap, /Retry in progress/);
  assert.match(heatmap, /retry\.hidden = corrupt/);
  assert.match(heatmap, /removeEventListener\("pointermove"/);
  assert.match(heatmap, /resources\?\.dispose\(\)/);
  assert.match(heatmap, /PPFD: \$\{Math\.round\(value\)\} µmol\/m²\/s/);
  assert.doesNotMatch(heatmap, /readPixels|readRenderTargetPixels|requestAnimationFrame/);
  assert.doesNotMatch(heatmap, /fixtureRoot|mounting_height|surfaceFlux/);
  const pointerHandler = heatmap.slice(
    heatmap.indexOf("const handlePointerMove"),
    heatmap.indexOf("const handlePointerLeave"),
  );
  assert.doesNotMatch(pointerHandler, /invalidate\(/);
  assert.match(
    html,
    /data-control="ppfdHeatmap"[\s\S]*type="checkbox"[\s\S]*checked[\s\S]*disabled/,
  );
  assert.match(html, /min="15"[\s\S]*max="100"[\s\S]*step="5"[\s\S]*value="70"/);
  assert.match(html, /id="ppfd-heatmap-legend"/);
  assert.match(html, /id="ppfd-heatmap-tooltip"/);
  assert.match(main, /createPpfdHeatmapController/);
  assert.match(main, /ppfdHeatmapController\?\.dispose\(\)/);
  assert.equal((main.match(/requestAnimationFrame\(/g) ?? []).length, 1);
});

test("D3 run scenes authenticate only the fixed schema-v2 surface metadata route", () => {
  const value = legacySchemaV2Scene();
  assert.equal("mounting_height" in value, false);
  value.surface_flux = {
    availability: "available",
    metadata: {
      filename: "surface-flux/metadata.v2.json",
      byte_length: 12345,
      sha256: "e".repeat(64),
    },
  };
  assert.equal(validateRunScene(value), value);
  assert.equal(validateSurfaceFluxSceneReference(value), value.surface_flux.metadata);
  value.surface_flux.metadata.filename = "surface-flux/metadata.v1.json";
  assert.throws(() => validateSurfaceFluxSceneReference(value), /incompatible/);
});

test("current optimized scenes admit only authenticated schema-v3 surface metadata", () => {
  const value = scene();
  value.surface_flux = {
    availability: "available",
    metadata: {
      filename: "surface-flux/metadata.v3.json",
      byte_length: 12345,
      sha256: "e".repeat(64),
    },
  };
  assert.equal(validateRunScene(value), value);
  assert.equal(validateSurfaceFluxSceneReference(value), value.surface_flux.metadata);
  value.surface_flux.metadata.filename = "surface-flux/metadata.v2.json";
  assert.throws(() => validateRunScene(value), /calibration does not cover/);
  assert.throws(() => validateSurfaceFluxSceneReference(value), /incompatible/);
});

test("legacy schema-v1 scenes dispatch only to their scalar metadata route", () => {
  const value = legacyScene();
  assert.equal("mounting_height" in value, false);
  value.surface_flux = {
    availability: "available",
    metadata: {
      filename: "surface-flux/metadata.v1.json",
      byte_length: 12345,
      sha256: "e".repeat(64),
    },
  };
  assert.equal(validateRunScene(value), value);
  assert.equal(validateSurfaceFluxSceneReference(value), value.surface_flux.metadata);
  value.surface_flux.metadata.filename = "surface-flux/metadata.v2.json";
  assert.throws(() => validateSurfaceFluxSceneReference(value), /incompatible/);
  value.viewer_resource_version = "unsupported";
  assert.throws(() => validateRunScene(value), /resource version/);
});

test("a legacy route name does not relax schema-v2 sampling validation", () => {
  const value = optimizedHistoricalScene();
  value.viewer_resource_version = LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION;
  delete value.profile.sampling_profile_id;
  value.surface_flux = {
    availability: "available",
    metadata: {
      filename: "surface-flux/metadata.v1.json",
      byte_length: 12345,
      sha256: "e".repeat(64),
    },
  };
  assert.throws(() => validateRunScene(value), /sampling profile/);
  value.profile.sampling_profile_id = LEGACY_SAMPLING_PROFILE;
  assert.equal(validateRunScene(value), value);
  assert.equal(validateSurfaceFluxSceneReference(value), value.surface_flux.metadata);
});

test("D2, D3, and D4 replay use exact profile-generation dispatch", () => {
  const current = scene();
  const d4 = optimizedHistoricalScene();
  const d3 = legacySchemaV2Scene();
  const d2 = legacyScene();
  const v3 = profile();
  const v2 = { ...profile(), schema_version: 2 };
  const v1 = legacyProfile();
  assert.equal(validateViewerGeneration(current, v3), undefined);
  assert.equal(validateViewerGeneration(d4, v3), undefined);
  assert.equal(validateViewerGeneration(d3, v2), undefined);
  assert.equal(validateViewerGeneration(d2, v1), undefined);
  assert.equal(validateViewerGeneration(d2, v2), undefined);
  assert.throws(() => validateViewerGeneration(current, v2), /incompatible/);
  assert.throws(() => validateViewerGeneration(d4, v2), /incompatible/);
  assert.throws(() => validateViewerGeneration(d3, v3), /incompatible/);
  assert.throws(() => validateViewerGeneration(d2, v3), /incompatible/);
});

test("optimized schema-v2 scenes fail closed when given historical surface flux", () => {
  const value = optimizedHistoricalScene();
  assert.equal("mounting_height" in value, false);
  const invalidMounting = optimizedHistoricalScene();
  invalidMounting.mounting_height = mountingHeight();
  assert.throws(() => validateRunScene(invalidMounting), /Historical scene cannot carry/);
  value.surface_flux = {
    availability: "available",
    metadata: {
      filename: "surface-flux/metadata.v2.json",
      byte_length: 12345,
      sha256: "e".repeat(64),
    },
  };
  assert.throws(() => validateRunScene(value), /calibration does not cover/);
  assert.throws(() => validateSurfaceFluxSceneReference(value), /surface-flux reference is incompatible/);
});

test("run scene has no selector or predefined-room assumptions and auto-loads fixtures", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/index.html", import.meta.url), "utf8",
  );
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  const artifacts = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/artifacts.js", import.meta.url), "utf8",
  );
  const fixtureArtifacts = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/fixture-artifacts.js", import.meta.url),
    "utf8",
  );
  const fixtureRenderer = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/fixture-renderer.js", import.meta.url),
    "utf8",
  );
  const combined = `${html}\n${main}\n${artifacts}\n${fixtureArtifacts}\n${fixtureRenderer}`;
  assert.doesNotMatch(html, /data-control="layout"/);
  assert.doesNotMatch(html, /data-control="surfaceMetric"/);
  assert.doesNotMatch(html, /data-control="surfaceSides"/);
  assert.doesNotMatch(html, />Mode\s*</);
  assert.doesNotMatch(html, /Surface view|>Both</);
  assert.match(html, /class="leaf-color-mode">Target Coverage</);
  assert.doesNotMatch(html, /value="target_coverage"|value="absorbed_par"/);
  assert.doesNotMatch(html, />Incident PAR</);
  assert.doesNotMatch(html, /id="surface-sides-control"/);
  assert.match(main, /surfaceFluxController\.setMetric\("absorbed_par"\)/);
  assert.match(main, /surfaceFluxController\.setSides\("both"\)/);
  assert.match(main, /targetCoverageController\?\.setEnabled\(false\)/);
  assert.match(main, /surfaceFluxController\?\.setEnabled\(false\)/);
  assert.doesNotMatch(main, /querySelector\([^\n]*(surfaceMetric|surfaceSides)|controls\.(surfaceMetric|surfaceSides)/);
  assert.doesNotMatch(combined, /default_layout|scene\.layouts|10x10|20x20|30x30/);
  assert.match(fixtureRenderer, /GLTFLoader/);
  assert.match(main, /loadValidatedFixtureArtifacts/);
  assert.match(html, /data-control="fixtures" type="checkbox" checked/);
  assert.match(main, /loadValidatedViewerArtifacts\("\.\/scene\.v1\.json"\)/);
  assert.doesNotMatch(main, /loadValidatedLayout|switchLayout|layoutCache/);
  assert.doesNotMatch(combined, /fixture[-_ ]?(scale|rotation|lod)/i);
});

test("leaf coloring uses one compact mode-specific legend without contract prose", () => {
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/index.html", import.meta.url), "utf8",
  );
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  const styles = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/styles.css", import.meta.url), "utf8",
  );
  assert.match(html, /id="surface-flux-state"[^>]+hidden/);
  assert.doesNotMatch(html, /surface-flux-provenance/);
  assert.match(main, /labels: \["Under target", "Target range", "Over target"\]/);
  assert.match(main, /Target: \$\{formatNumber\(/);
  assert.match(main, /Requested target/);
  assert.match(main, /Achieved Stage A mean/);
  assert.match(main, /FSPM override/);
  assert.match(main, /bar\.style\.backgroundImage = continuousGradient/);
  assert.match(styles, /\.leaf-color-bar/);
  assert.match(main, /surfaceFluxLegends\.replaceChildren\(\);[\s\S]+renderTargetCoverageContract\(\)/);
  assert.match(main, /surfaceFluxController\.setMetric\("absorbed_par"\)/);
  assert.match(main, /surfaceFluxController\.setSides\("both"\)/);
  assert.match(main, /targetCoverageController\.setEnabled\(true\)/);
  assert.doesNotMatch(main, /renderSurfaceFluxLegend|surfaceMetric|surfaceSides|surfaceSidesControl/);
  assert.doesNotMatch(main, /Metadata schema:|Classification basis:|Reference policy:|Interpolation:/);
  assert.doesNotMatch(main, /coefficient_count|beta_provenance|raw_minimum_q|physical_threshold_rule/);
  assert.doesNotMatch(styles, /surface-legend-row|surface-swatch|surface-legend-note/);
});

test("surface-flux lifecycle forwards abort and releases every promoted payload", () => {
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  assert.match(main, /const eventAbort = new AbortController\(\);/);
  assert.match(main, /signal: eventAbort\.signal/);
  assert.match(main, /eventAbort\.abort\(\);/);
  assert.match(main, /surfaceFluxController\?\.dispose\(\);/);
  assert.match(main, /surfaceFluxPayload\?\.release\?\.\(\);/);
  assert.match(main, /const surfaceFluxPromise = loadValidatedSurfaceFluxArtifacts/);
  assert.match(main, /surfaceFluxPayload = result\.payload \?\? null;/);
  assert.match(
    main,
    /catch \(error\) \{[\s\S]*await surfaceFluxPromise\.catch\(\(\) => null\);[\s\S]*surfaceFluxPayload\?\.release/,
  );
  assert.match(main, /if \(disposed\) \{[\s\S]*surfaceFluxPayload\?\.release/);
  assert.match(main, /clearSurfaceFluxColoring\(surface\);/);
  assert.match(main, /surfaceFluxController\.setSides\("both"\)/);
});

test("instance translation contract rejects type stride count length and NaN", () => {
  const artifact = scene().plant_instances.instance_translations;
  const buffer = new ArrayBuffer(6 * 12);
  assert.equal(parseInstanceTranslations(buffer, artifact).count, 6);
  for (const changed of [
    { component_type: "float64" }, { stride_bytes: 16 },
    { count: 5 }, { byte_length: 1 },
  ]) {
    assert.throws(
      () => parseInstanceTranslations(buffer, { ...artifact, ...changed }),
      /length, count, type, or stride/,
    );
  }
  new DataView(buffer).setFloat32(0, Number.NaN, true);
  assert.throws(() => parseInstanceTranslations(buffer, artifact), /non-finite/);
});

test("one InstancedMesh reuses canonical topology and exact run count", () => {
  const attributes = {
    POSITION: new Float32Array(9), NORMAL: new Float32Array(9),
    _LEAF_INDEX: new Uint32Array(3), _FACE_INDEX: new Uint32Array(3),
    _PATCH_INDEX: new Uint32Array(3),
  };
  const surface = createPlantSurface({ attributes }, 6);
  applyInstanceTranslations(surface, { count: 6, values: new Float32Array(18) });
  assert.equal(surface.isInstancedMesh, true);
  assert.equal(surface.count, 6);
  assert.equal(surface.geometry.getAttribute("position").array, attributes.POSITION);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("D4 leaf material keeps one PBR material and bounded deterministic detail", () => {
  const defaultCompile = THREE.MeshStandardMaterial.prototype.onBeforeCompile;
  const defaultCacheKey = THREE.MeshStandardMaterial.prototype.customProgramCacheKey;
  const attributes = {
    POSITION: new Float32Array(9), NORMAL: new Float32Array(9),
    TEXCOORD_0: new Float32Array([0, 0.5, 0.5, 0, 1, 0.5]),
    _LEAF_INDEX: new Uint32Array([3, 3, 3]), _FACE_INDEX: new Uint32Array(3),
    _PATCH_INDEX: new Uint32Array(3),
  };
  const surface = createPlantSurface({ attributes }, 2);
  assert.equal(surface.isInstancedMesh, true);
  assert.equal(surface.children.length, 0);
  assert.equal(Array.isArray(surface.material), false);
  assert.equal(surface.material.isMeshStandardMaterial, true);
  assert.equal(surface.material.color.getHex(), 0x356b42);
  assert.equal(surface.material.emissive.getHex(), 0x000000);
  assert.equal(surface.material.metalness, 0);
  assert.equal(surface.material.roughness, 0.82);
  assert.deepEqual(LEAF_MATERIAL_POLICY, {
    frontColor: "#356B42",
    undersideColor: "#3B6848",
    undersideSaturation: 0.94,
    frontRoughness: 0.82,
    undersideRoughness: 0.84,
    metalness: 0,
    leafVariationMaximum: 0.03,
    midribContrastMaximum: 0.06,
    secondaryVeinContrastMaximum: 0.03,
    fluxVeinContrastMaximum: 0.01,
    midribRoughnessOffset: -0.018,
    secondaryVeinRoughnessOffset: -0.01,
    fluxRoughnessDetailScale: 0.35,
    normalDetailHeightM: 0.00006,
    normalDetailMaximumDegrees: 2,
    fluxNormalDetailMaximumDegrees: 0.5,
  });
  const shader = leafShaderStub();
  surface.material.onBeforeCompile(shader, {});
  assert.match(shader.vertexShader, /vLeafIndex = _leaf_index;/);
  assert.match(shader.fragmentShader, /fwidth\(distanceToCurve\)/);
  assert.equal((shader.fragmentShader.match(/leafVeinPair\(uv,/g) ?? []).length, 6);
  assert.match(shader.fragmentShader, /gl_FrontFacing/);
  assert.doesNotMatch(shader.fragmentShader, /gl_InstanceID|random|noise|sin\s*\(/i);
  assert.match(shader.fragmentShader, /leafScientificOverlayVisible/);
  setLeafScientificOverlayVisible(surface, true);
  assert.equal(shader.uniforms.leafScientificOverlayVisible.value, 1);
  assert.equal(
    surface.userData.leafMaterialController.getState().scientificOverlayVisible,
    true,
  );
  setLeafScientificOverlayVisible(surface, false);
  assert.equal(shader.uniforms.leafScientificOverlayVisible.value, 0);
  surface.userData.leafMaterialController.dispose();
  assert.equal(surface.userData.leafMaterialController, undefined);
  assert.equal(surface.material.userData.leafMaterialShader, undefined);
  assert.equal(surface.material.onBeforeCompile, defaultCompile);
  assert.equal(surface.material.customProgramCacheKey, defaultCacheKey);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("SHA-256 verification rejects substituted bytes before parsing", async () => {
  const bytes = new TextEncoder().encode("abc").buffer;
  const digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
  assert.equal(await verifyHash(bytes, digest, "artifact"), digest);
  await assert.rejects(
    verifyHash(new TextEncoder().encode("substituted").buffer, digest, "artifact"),
    /SHA-256 validation/,
  );
});

test("receiver parsing and lazy overlays retain exact contracts", () => {
  const buffer = new ArrayBuffer(2 * 6 * 4);
  const view = new DataView(buffer);
  for (let index = 0; index < 12; index += 1) view.setFloat32(index * 4, index + 0.25, true);
  const parsed = parseReceiverBuffer(buffer, 2);
  assert.deepEqual([...parsed.positions], [0.25, 1.25, 2.25, 6.25, 7.25, 8.25]);
  assert.throws(() => parseReceiverBuffer(buffer.slice(0, -1), 2), /length/);
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  assert.match(main, /if \(!receiverLayer\)[\s\S]*createReceiverLayer/);
  assert.match(main, /receiverLayer\.ensureNormals/);
  assert.match(
    main,
    /Boolean\(receiverLayer\?\.points\.visible \|\| receiverNormals\?\.visible\)/,
  );
  assert.equal((main.match(/updateLeafScientificOverlayState\(\);/g) ?? []).length, 2);
});

test("stable picking resolves run-specific plant ordering", () => {
  const value = identity();
  assert.equal(validateIdentityMap(value), value);
  assert.equal(validateIdentityMap(legacyIdentity()).schema_version, 1);
  const missingSampling = identity();
  delete missingSampling.sampling_profile_id;
  assert.throws(() => validateIdentityMap(missingSampling), /sampling profile/);
  assert.equal(
    resolveSurfaceIdentity(value, {
      instanceId: 2, leafIndex: 0, faceIndex: 0, patchIndex: 0,
    }, ["run-plant-0", "run-plant-1", "run-plant-2"]).plantId,
    "run-plant-2",
  );
  const geometry = {
    attributes: {
      _LEAF_INDEX: new Uint32Array(1920 * 3),
      _FACE_INDEX: Uint32Array.from(
        { length: 1920 * 3 }, (_, index) => Math.floor(index / 3),
      ),
      _PATCH_INDEX: new Uint32Array(1920 * 3),
    },
  };
  assert.equal(validateGeometryIdentity(scene(), geometry, value), true);
  assert.throws(
    () => validateGeometryIdentity(scene(), geometry, legacyIdentity()),
    /sampling identities do not agree/,
  );
  geometry.attributes._PATCH_INDEX[3] = 1;
  assert.throws(() => validateGeometryIdentity(scene(), geometry, value), /do not agree/);
});

test("GLB parser requires exact finite D4 UV accessors and values", () => {
  const valid = scientificGlb();
  const parsed = parseScientificGlb(valid, 1, 3);
  assert.deepEqual([...parsed.attributes.TEXCOORD_0], [0, 0.5, 0.5, 0, 1, 0.5]);
  for (const changed of [
    { includeUv: false },
    { uvType: "VEC3" },
    { uvComponentType: 5125 },
    { uvCount: 2 },
    { uvMin: [0, 0.5] },
    { uvValues: [0, 0.5, 0.5, 0, 1.01, 0.5] },
    { uvValues: [0, 0.5, 0.5, Number.NaN, 1, 0.5] },
  ]) {
    assert.throws(
      () => parseScientificGlb(scientificGlb(changed), 1, 3),
      /TEXCOORD|primitive/,
    );
  }
  assert.equal(
    parseScientificGlb(scientificGlb({ includeUv: false }), 1, 2)
      .attributes.TEXCOORD_0,
    undefined,
  );
});

test("GLB parser rejects malformed data and viewer retains cameras and controls", () => {
  assert.throws(() => parseScientificGlb(new ArrayBuffer(12)), /truncated/);
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/index.html", import.meta.url), "utf8",
  );
  for (const control of [
    "reset", "top", "side", "surface", "bounds",
    "fixtures", "receivers", "normals",
  ]) assert.match(html, new RegExp(`data-control="${control}"`));
  assert.doesNotMatch(html, /data-control="footprint"/);
  assert.doesNotMatch(html, /data-control="reference"/);
  assert.doesNotMatch(html, /https?:\/\/|react|vue|angular/i);
  const renderer = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/renderer.js", import.meta.url), "utf8",
  );
  assert.equal((renderer.match(/new THREE\.InstancedMesh/g) ?? []).length, 1);
  assert.equal((renderer.match(/new THREE\.Points\(/g) ?? []).length, 1);
  assert.doesNotMatch(renderer, /computeVertexNormals/);
});

test("fixture catalog supports exactly ten approved run-local assets", () => {
  assert.equal(Object.keys(APPROVED_FIXTURE_ASSETS).length, 10);
  assert.deepEqual(APPROVED_FIXTURE_ASSETS["proposed-led-module-v1"], {
    systemId: "proposed",
    displayFixtureType: "standalone_module",
    resourcePath: "fixtures/proposed/led_module.glb",
    byteSize: 88540,
    sha256: "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445",
    alphaModes: ["OPAQUE"],
    alphaFactors: [1],
  });
  assert.deepEqual(APPROVED_FIXTURE_ASSETS["proposed-corner3-v1"], {
    systemId: "proposed",
    displayFixtureType: "corner3",
    resourcePath: "fixtures/proposed/corner3.glb",
    byteSize: 238648,
    sha256: "e30f98457b1af1725843ed0c42edfc9d22516445c602f4edcc8b8ed116c7d243",
    alphaModes: ["OPAQUE"],
    alphaFactors: [1],
  });
  assert.deepEqual(APPROVED_FIXTURE_ASSETS["hps-housing-v3"], {
    systemId: "hps",
    displayFixtureType: "hps_1000w_fixture",
    resourcePath: "fixtures/hps/hps.glb",
    byteSize: 353160,
    sha256: "083df1475e84e9552d5ec548fc34669092b5ba4284fcf33ebf7dbb2f26439d7d",
    alphaModes: ["BLEND", "OPAQUE"],
    alphaFactors: [0.349999994, 1],
    placementContract: {
      identitySha256: "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293",
      metersPerAssetUnit: 0.001,
      dimensionCorrectionScaleXyz: [1, 1, 1],
      placementPlaneLocalYmm: -248.92,
      placementCorrectionLocalXyzMm: [0, 248.92, 0],
      pivotContract: (
        "housing_bottom_shifted_to_separate_scientific_luminous_aperture_plane"
      ),
    },
  });
  const value = validateFixtureCatalog(fixtureCatalog(), scene());
  assert.equal(value.schema_version, 2);
  assert.deepEqual(value.mounting_height, mountingHeight());
  assert.equal(value.fixture_count, 6);
  assert.equal(value.asset_groups.length, 1);
  assert.throws(
    () => validateFixtureCatalog({ ...fixtureCatalog(), run: {
      ...fixtureCatalog().run, system_id: "proposed",
    } }, scene()),
    /run or system identity/,
  );
  assert.throws(
    () => validateFixtureCatalog({
      ...fixtureCatalog(), authoritative_layout_sha256: "7".repeat(64),
    }, scene()),
    /layout or plan identity/,
  );
});

test("fixture catalog accepts validated Proposed layout composition identity", () => {
  const catalog = fixtureCatalog();
  const proposedScene = scene();
  const assetId = "proposed-linear2-v1";
  const approved = APPROVED_FIXTURE_ASSETS[assetId];
  const group = catalog.asset_groups[0];
  catalog.run.system_id = "proposed";
  proposedScene.run.system_id = "proposed";
  catalog.proposed_layout_mode = "linear";
  catalog.fixture_policy_id = "proposed_linear_fixture_assemblies_v1";
  Object.assign(catalog.fixture_plan, {
    system_id: "proposed",
    proposed_layout_mode: catalog.proposed_layout_mode,
    fixture_policy_id: catalog.fixture_policy_id,
    display_classification_policy: "proposed_member_connector_topology_v2",
  });
  catalog.fixture_plan.fixtures.forEach((fixture) => Object.assign(fixture, {
    display_asset_id: assetId,
    display_fixture_type: approved.displayFixtureType,
  }));
  Object.assign(group, {
    display_asset_id: assetId,
    display_fixture_type: approved.displayFixtureType,
  });
  Object.assign(group.asset, {
    filename: `assets/${assetId}-${approved.sha256.slice(0, 16)}.glb`,
    byte_size: approved.byteSize,
    sha256: approved.sha256,
  });
  Object.assign(group.registry, {
    asset_id: assetId,
    approved_system_id: approved.systemId,
    approved_display_fixture_type: approved.displayFixtureType,
    packaged_resource_path: approved.resourcePath,
    expected_byte_size: approved.byteSize,
    expected_sha256: approved.sha256,
  });
  group.instance_matrices.filename = (
    `transforms/${assetId}-${catalog.fixture_plan_sha256.slice(0, 16)}.f32le.bin`
  );

  assert.equal(validateFixtureCatalog(catalog, proposedScene), catalog);
  catalog.fixture_plan.fixture_policy_id = "proposed_tile_fill_connect_fixture_assemblies_v1";
  assert.throws(
    () => validateFixtureCatalog(catalog, proposedScene),
    /plan identity or ordering/,
  );
});

test("fixture catalog authenticates the HPS aperture placement contract", () => {
  const catalog = fixtureCatalog();
  const hpsScene = scene();
  const assetId = "hps-housing-v3";
  const approved = APPROVED_FIXTURE_ASSETS[assetId];
  const group = catalog.asset_groups[0];
  catalog.run.system_id = "hps";
  hpsScene.run.system_id = "hps";
  catalog.fixture_plan.system_id = "hps";
  catalog.fixture_plan.fixtures.forEach((fixture) => Object.assign(fixture, {
    display_asset_id: assetId,
    display_fixture_type: approved.displayFixtureType,
    placement_plane: "luminous_aperture_plane_separate_from_housing",
    placement_contract_sha256: approved.placementContract.identitySha256,
  }));
  Object.assign(group, {
    display_asset_id: assetId,
    display_fixture_type: approved.displayFixtureType,
  });
  Object.assign(group.asset, {
    filename: `assets/${assetId}-${approved.sha256.slice(0, 16)}.glb`,
    byte_size: approved.byteSize,
    sha256: approved.sha256,
  });
  Object.assign(group.registry, {
    asset_id: assetId,
    approved_system_id: approved.systemId,
    approved_display_fixture_type: approved.displayFixtureType,
    packaged_resource_path: approved.resourcePath,
    expected_byte_size: approved.byteSize,
    expected_sha256: approved.sha256,
    dimension_correction_scale_xyz: [1, 1, 1],
    pivot: {
      contract: approved.placementContract.pivotContract,
      placement_plane_local_y_mm: -248.92,
      placement_correction_local_x_mm: 0,
      placement_correction_local_y_mm: 248.92,
      placement_correction_local_z_mm: 0,
    },
    material_alpha_modes: ["BLEND", "OPAQUE"],
    approved_base_color_alpha_factors: [0.349999994, 1],
  });
  group.instance_matrices.filename = (
    `transforms/${assetId}-${catalog.fixture_plan_sha256.slice(0, 16)}.f32le.bin`
  );

  assert.equal(validateFixtureCatalog(catalog, hpsScene), catalog);
  const wrongIdentity = structuredClone(catalog);
  wrongIdentity.fixture_plan.fixtures[0].placement_contract_sha256 = "0".repeat(64);
  assert.throws(
    () => validateFixtureCatalog(wrongIdentity, hpsScene),
    /placement identity/,
  );
  const fittedCorrection = structuredClone(catalog);
  fittedCorrection.asset_groups[0].registry.pivot
    .placement_correction_local_y_mm = 248.919998;
  assert.throws(
    () => validateFixtureCatalog(fittedCorrection, hpsScene),
    /HPS placement contract/,
  );
});

test("fixture catalog authenticates standalone module policy and asset", () => {
  const catalog = fixtureCatalog();
  const proposedScene = scene();
  const assetId = "proposed-led-module-v1";
  const approved = APPROVED_FIXTURE_ASSETS[assetId];
  const group = catalog.asset_groups[0];
  catalog.run.system_id = "proposed";
  proposedScene.run.system_id = "proposed";
  catalog.proposed_layout_mode = "standalone_modules";
  catalog.fixture_policy_id = (
    "proposed_standalone_module_instances_with_alignment_lattice_v2"
  );
  catalog.proposed_ring_mode = "reduced_one_ring";
  catalog.module_pattern_id = "centered_square_reduced_one_ring_v1";
  Object.assign(catalog.fixture_plan, {
    system_id: "proposed",
    proposed_layout_mode: catalog.proposed_layout_mode,
    fixture_policy_id: catalog.fixture_policy_id,
    proposed_ring_mode: catalog.proposed_ring_mode,
    module_pattern_id: catalog.module_pattern_id,
    display_classification_policy: (
      "proposed_standalone_module_with_alignment_lattice_identity_v2"
    ),
    alignment_lattice_identity_sha256: "4".repeat(64),
  });
  catalog.alignment_lattice_identity_sha256 = "4".repeat(64);
  catalog.alignment_lattice = alignmentLattice(catalog.fixture_count);
  catalog.fixture_plan.fixtures.forEach((fixture) => Object.assign(fixture, {
    display_asset_id: assetId,
    display_fixture_type: approved.displayFixtureType,
  }));
  Object.assign(group, {
    display_asset_id: assetId,
    display_fixture_type: approved.displayFixtureType,
  });
  Object.assign(group.asset, {
    filename: `assets/${assetId}-${approved.sha256.slice(0, 16)}.glb`,
    byte_size: approved.byteSize,
    sha256: approved.sha256,
  });
  Object.assign(group.registry, {
    asset_id: assetId,
    approved_system_id: approved.systemId,
    approved_display_fixture_type: approved.displayFixtureType,
    packaged_resource_path: approved.resourcePath,
    expected_byte_size: approved.byteSize,
    expected_sha256: approved.sha256,
  });
  group.instance_matrices.filename = (
    `transforms/${assetId}-${catalog.fixture_plan_sha256.slice(0, 16)}.f32le.bin`
  );

  assert.equal(validateFixtureCatalog(catalog, proposedScene), catalog);
  const incompleteIdentity = structuredClone(catalog);
  delete incompleteIdentity.proposed_layout_mode;
  delete incompleteIdentity.fixture_policy_id;
  assert.throws(
    () => validateFixtureCatalog(incompleteIdentity, proposedScene),
    /Proposed ring identity/,
  );
  catalog.module_pattern_id = "centered_square_full_v1";
  assert.throws(
    () => validateFixtureCatalog(catalog, proposedScene),
    /Proposed ring identity/,
  );
  catalog.module_pattern_id = "centered_square_reduced_one_ring_v1";
  catalog.fixture_plan.display_classification_policy = (
    "proposed_member_connector_topology_v2"
  );
  assert.throws(
    () => validateFixtureCatalog(catalog, proposedScene),
    /plan identity or ordering/,
  );
});

test("fixture matrices are exact 64-byte little-endian column-major records", () => {
  const bytes = new ArrayBuffer(64);
  const view = new DataView(bytes);
  const expected = Array.from({ length: 16 }, (_, index) => index + 0.25);
  expected.forEach((value, index) => view.setFloat32(index * 4, value, true));
  const record = fixtureCatalog().asset_groups[0].instance_matrices;
  const one = { ...record, count: 1, byte_length: 64 };
  const parsed = parseFixtureMatrices(bytes, one);
  assert.deepEqual([...parsed.values], expected);
  view.setFloat32(0, Number.NaN, true);
  assert.throws(() => parseFixtureMatrices(bytes, one), /non-finite/);
  assert.throws(
    () => parseFixtureMatrices(new ArrayBuffer(63), one),
    /length, count, type, or convention/,
  );
});

test("fixture GLB validation rejects secondary URIs before loader parsing", () => {
  const approved = APPROVED_FIXTURE_ASSETS["conventional-led-8-bar-v1"];
  const document = {
    asset: { version: "2.0" }, buffers: [{ byteLength: 4 }],
    materials: [{ doubleSided: true }], meshes: [{ primitives: [{ material: 0 }] }],
    nodes: [{ mesh: 0 }], scenes: [{ nodes: [0] }], scene: 0,
  };
  assert.deepEqual(validateEmbeddedFixtureGlb(fixtureGlb(document), approved), document);
  assert.throws(
    () => validateEmbeddedFixtureGlb(fixtureGlb({
      ...document, buffers: [{ byteLength: 4, uri: "other.bin" }],
    }), approved),
    /secondary resource URIs/,
  );
  assert.throws(
    () => validateEmbeddedFixtureGlb(fixtureGlb({
      ...document, animations: [{}],
    }), approved),
    /prohibited scene content/,
  );
  const hpsDocument = {
    ...document,
    materials: [
      {
        doubleSided: true,
        alphaMode: "BLEND",
        pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 0.349999994] },
      },
      { doubleSided: true },
    ],
    meshes: [{ primitives: [{ material: 0 }, { material: 1 }] }],
  };
  assert.deepEqual(
    validateEmbeddedFixtureGlb(
      fixtureGlb(hpsDocument),
      APPROVED_FIXTURE_ASSETS["hps-housing-v3"],
    ),
    hpsDocument,
  );
});

test("fixture root baking, parity draw scaling, disposal, and event rendering are explicit", () => {
  const fixtureRenderer = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/fixture-renderer.js", import.meta.url),
    "utf8",
  );
  const main = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/main.js", import.meta.url), "utf8",
  );
  assert.match(
    fixtureRenderer,
    /geometry\.applyMatrix4\(authoredPostRootWorldMatrix\)/,
  );
  assert.match(fixtureRenderer, /multiplyMatrices\(serverMatrix, createAssetRootReflection\(\)\)/);
  assert.match(fixtureRenderer, /fixtureMatrixParity\(submittedMatrix\) !== 1/);
  assert.equal((fixtureRenderer.match(/new THREE\.InstancedMesh/g) ?? []).length, 3);
  assert.match(fixtureRenderer, /assetGroup\.matrices\.count/);
  assert.match(fixtureRenderer, /partition\.sourceInstanceIds\.length/);
  assert.match(fixtureRenderer, /new THREE\.CircleGeometry\(active\.diameter_m \/ 2, 32\)/);
  assert.match(fixtureRenderer, /active-centered-cob-les/);
  assert.match(fixtureRenderer, /smd_emitter_plane_active !== false/);
  assert.match(main, /artifacts\.scene\.run\.information\?\.proposed_source/);
  assert.match(fixtureRenderer, /layers\.set\(FIXTURE_RENDER_LAYER\)/);
  assert.match(fixtureRenderer, /ownedGeometries[\s\S]*\.dispose\(\)/);
  assert.match(fixtureRenderer, /ownedMaterials[\s\S]*\.dispose\(\)/);
  assert.match(main, /raycaster\.layers\.set\(0\)/);
  assert.match(main, /pagehide/);
  assert.equal((main.match(/requestAnimationFrame\(/g) ?? []).length, 1);
  assert.doesNotMatch(main, /setAnimationLoop|function animate|while \(true\)/);
});

test("alignment lattice uses one exact-scale instanced non-emitting cylinder mesh", () => {
  const root = new THREE.Group();
  const bounds = new THREE.Box3();
  const geometries = new Set();
  const materials = new Set();
  const lattice = alignmentLattice(6);
  assert.equal(addAlignmentLattice({
    bounds,
    lattice,
    ownedGeometries: geometries,
    ownedMaterials: materials,
    root,
  }), 1);
  assert.equal(root.children.length, 1);
  const mesh = root.children[0];
  assert.equal(mesh.isInstancedMesh, true);
  assert.equal(mesh.count, lattice.links.length);
  assert.equal(mesh.material.emissive.getHex(), 0);
  assert.equal(mesh.userData.materialId, "fixture_body_anodized_aluminum_v2");
  assert.equal(mesh.userData.geometryIdentitySha256, lattice.identity_sha256);
  assert.equal(mesh.geometry.parameters.radiusTop * 2, 0.0015875);
  assert.equal(mesh.geometry.parameters.radiusBottom * 2, 0.0015875);
  assert.equal(mesh.geometry.parameters.height, 1);
});

test("asset-root parity factorization preserves exact vertices and bounds", () => {
  const negativeServer = new THREE.Matrix4().set(
    0.001, 0, 0, 1.25,
    0, 0, 0.00125, 0.4572,
    0, 0.0008, 0, -2.5,
    0, 0, 0, 1,
  );
  const authored = new THREE.Matrix4().compose(
    new THREE.Vector3(100, -25, 40),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(-0.71, 0.37, 1.12)),
    new THREE.Vector3(0.08, 0.11, 0.065),
  );
  const positiveServer = negativeServer.clone().multiply(
    new THREE.Matrix4().makeScale(-1, 1, 1),
  );
  const negativeServerBefore = negativeServer.clone();
  const positiveServerBefore = positiveServer.clone();
  const authoredBefore = authored.clone();
  const source = new THREE.BufferGeometry();
  source.setAttribute("position", new THREE.Float32BufferAttribute([
    -17, -3, -1, 2, 5, 11, 23, -7, 4,
  ], 3));
  source.setAttribute("normal", new THREE.Float32BufferAttribute([
    0, 1, 0, 0, 1, 0, 0, 1, 0,
  ], 3));
  source.computeBoundingBox();
  const rootGeometry = bakeFixturePrimitiveToAssetRoot(source, null, authored);
  const reflectedRootGeometry = createReflectedFixtureRootGeometry(rootGeometry);
  const reflectedInstance = composeReflectedFixtureInstanceMatrix(
    new THREE.Matrix4(), negativeServer,
  );
  assert.equal(fixtureMatrixParity(positiveServer), 1);
  assert.equal(fixtureMatrixParity(negativeServer), -1);
  assert.equal(fixtureMatrixParity(reflectedInstance), 1);

  for (let index = 0; index < 3; index += 1) {
    const local = new THREE.Vector3().fromBufferAttribute(
      source.getAttribute("position"), index,
    );
    const expectedPositive = local.clone().applyMatrix4(authored)
      .applyMatrix4(positiveServer);
    const renderedPositive = new THREE.Vector3().fromBufferAttribute(
      rootGeometry.getAttribute("position"), index,
    ).applyMatrix4(positiveServer);
    assert.ok(expectedPositive.distanceTo(renderedPositive) <= 1.0e-7);

    const reflectedIndex = index === 1 ? 2 : index === 2 ? 1 : 0;
    const expectedNegative = local.clone().applyMatrix4(authored)
      .applyMatrix4(negativeServer);
    const renderedNegative = new THREE.Vector3().fromBufferAttribute(
      reflectedRootGeometry.getAttribute("position"), reflectedIndex,
    ).applyMatrix4(reflectedInstance);
    assert.ok(expectedNegative.distanceTo(renderedNegative) <= 1.0e-7);
  }

  rootGeometry.computeBoundingBox();
  reflectedRootGeometry.computeBoundingBox();
  const positiveBounds = transformAttributeBounds(
    rootGeometry.getAttribute("position"), [positiveServer],
  );
  const expectedPositiveBounds = transformAttributeBounds(
    source.getAttribute("position"), [authored, positiveServer],
  );
  assertBoxClose(positiveBounds, expectedPositiveBounds, 1.0e-7);
  const negativeBounds = transformAttributeBounds(
    reflectedRootGeometry.getAttribute("position"), [reflectedInstance],
  );
  const expectedNegativeBounds = transformAttributeBounds(
    source.getAttribute("position"), [authored, negativeServer],
  );
  assertBoxClose(negativeBounds, expectedNegativeBounds, 1.0e-7);
  assert.deepEqual(negativeServer.elements, negativeServerBefore.elements);
  assert.deepEqual(positiveServer.elements, positiveServerBefore.elements);
  assert.deepEqual(authored.elements, authoredBefore.elements);
  source.dispose();
  rootGeometry.dispose();
  reflectedRootGeometry.dispose();
});

test("primitive cloning cannot accumulate geometry mutations across renderer scopes", () => {
  const source = new THREE.BufferGeometry();
  source.setAttribute("position", new THREE.Float32BufferAttribute([
    -2, -1, 0, 3, -1, 0, 3, 4, 2,
    -7, 5, 1, 8, 6, -3, 2, 9, 4,
  ], 3));
  source.addGroup(0, 3, 0);
  source.addGroup(3, 3, 1);
  const sourcePositions = [...source.getAttribute("position").array];
  const sourceGroups = source.groups.map((group) => ({ ...group }));

  const clones = [
    cloneFixturePrimitiveGeometry(source, source.groups[0]),
    cloneFixturePrimitiveGeometry(source, source.groups[1]),
    cloneFixturePrimitiveGeometry(source, source.groups[0]),
    cloneFixturePrimitiveGeometry(source, source.groups[1]),
  ];
  clones[0].getAttribute("position").setX(0, 999);
  clones[0].translate(50, -30, 20);

  assert.deepEqual([...source.getAttribute("position").array], sourcePositions);
  assert.deepEqual(source.groups, sourceGroups);
  assert.deepEqual([...clones[2].getAttribute("position").array], sourcePositions);
  assert.deepEqual([...clones[3].getAttribute("position").array], sourcePositions);
  assert.deepEqual(clones.map((geometry) => geometry.groups.length), [0, 0, 0, 0]);
  assert.deepEqual(
    clones.map((geometry) => [geometry.drawRange.start, geometry.drawRange.count]),
    [[0, 3], [3, 3], [0, 3], [3, 3]],
  );
  for (const geometry of clones) geometry.dispose();
  source.dispose();
});

test("asset-root baking promotes cloned quantized transform attributes before translation", () => {
  const source = new THREE.BufferGeometry();
  source.setAttribute("position", new THREE.Int16BufferAttribute([
    0, 0, 0, 32767, 0, 0, 0, 32767, 0,
  ], 3, true));
  source.setAttribute("normal", new THREE.Int8BufferAttribute([
    0, 0, 127, 0, 0, 127, 0, 0, 127,
  ], 3, true));
  const sourcePositions = [...source.getAttribute("position").array];
  const authored = new THREE.Matrix4().makeTranslation(125, -37, 82);
  const root = bakeFixturePrimitiveToAssetRoot(source, null, authored);
  assert.equal(root.getAttribute("position").array.constructor, Float32Array);
  assert.equal(root.getAttribute("normal").array.constructor, Float32Array);
  assert.deepEqual(
    new THREE.Vector3().fromBufferAttribute(root.getAttribute("position"), 0).toArray(),
    [125, -37, 82],
  );
  assert.deepEqual([...source.getAttribute("position").array], sourcePositions);
  source.dispose();
  root.dispose();
});

test("reflected indexed and non-indexed winding agrees with normals and tangents", () => {
  for (const indexed of [true, false]) {
    const source = new THREE.BufferGeometry();
    source.setAttribute("position", new THREE.Float32BufferAttribute([
      0, 0, 0, 1, 0, 0, 0, 1, 0,
    ], 3));
    source.setAttribute("normal", new THREE.Float32BufferAttribute([
      0, 0, 1, 0, 0, 1, 0, 0, 1,
    ], 3));
    source.setAttribute("tangent", new THREE.Float32BufferAttribute([
      1, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 1,
    ], 4));
    if (indexed) source.setIndex([0, 1, 2]);
    const sourcePositions = [...source.getAttribute("position").array];
    const sourceTangents = [...source.getAttribute("tangent").array];
    const authored = new THREE.Matrix4().compose(
      new THREE.Vector3(4, -3, 2),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(0.31, -0.52, 0.77)),
      new THREE.Vector3(1.7, 0.6, 1.25),
    );
    const root = bakeFixturePrimitiveToAssetRoot(source, null, authored);
    const reflected = createReflectedFixtureRootGeometry(root);
    const triangle = reflected.getIndex()
      ? [0, 1, 2].map((offset) => reflected.getIndex().getX(offset))
      : [0, 1, 2];
    const positions = reflected.getAttribute("position");
    const first = new THREE.Vector3().fromBufferAttribute(positions, triangle[0]);
    const second = new THREE.Vector3().fromBufferAttribute(positions, triangle[1]);
    const third = new THREE.Vector3().fromBufferAttribute(positions, triangle[2]);
    const faceNormal = second.clone().sub(first).cross(third.clone().sub(first)).normalize();
    const normals = reflected.getAttribute("normal");
    for (const vertexIndex of triangle) {
      const normal = new THREE.Vector3().fromBufferAttribute(normals, vertexIndex);
      assert.ok(faceNormal.dot(normal) > 0.999999);
    }
    const tangents = reflected.getAttribute("tangent");
    for (let index = 0; index < tangents.count; index += 1) {
      assert.equal(tangents.getW(index), -1);
    }
    assert.deepEqual([...source.getAttribute("position").array], sourcePositions);
    assert.deepEqual([...source.getAttribute("tangent").array], sourceTangents);
    source.dispose();
    root.dispose();
    reflected.dispose();
  }
});

test("actual Proposed GLB root baking retains exact vertices and tight bounds", async () => {
  const representativeAssets = [
    "proposed-centerpiece-v1",
    "proposed-linear3-v1",
    "proposed-linear4-v1",
    "proposed-corner3-v1",
    "proposed-reverse-l-v1",
  ];
  const server = new THREE.Matrix4().set(
    0.00113, 0, 0, 1.19,
    0, 0, 0.00113, 0.4572,
    0, 0.00113, 0, -1.04,
    0, 0, 0, 1,
  );
  for (const assetId of representativeAssets) {
    const approved = APPROVED_FIXTURE_ASSETS[assetId];
    const gltf = await loadActualFixtureGlb(approved.resourcePath);
    gltf.scene.updateMatrixWorld(true);
    let sourceMesh = null;
    gltf.scene.traverse((node) => {
      if (sourceMesh || !node.isMesh || !node.geometry?.getAttribute("position")) return;
      const translationMagnitude = Math.hypot(
        node.matrixWorld.elements[12],
        node.matrixWorld.elements[13],
        node.matrixWorld.elements[14],
      );
      const hasRotation = [1, 2, 4, 6, 8, 9]
        .some((index) => Math.abs(node.matrixWorld.elements[index]) > 1.0e-8);
      if (translationMagnitude > 1.0e-6 && hasRotation) sourceMesh = node;
    });
    assert.ok(sourceMesh, `${assetId} must exercise authored translation and rotation`);
    const materialsBefore = Array.isArray(sourceMesh.material)
      ? [...sourceMesh.material] : [sourceMesh.material];
    const materialValuesBefore = materialsBefore.map(fixtureMaterialSnapshot);
    const position = sourceMesh.geometry.getAttribute("position");
    const positionsBefore = [...position.array];
    const rootGeometry = bakeFixturePrimitiveToAssetRoot(
      sourceMesh.geometry, null, sourceMesh.matrixWorld,
    );
    const reflectedGeometry = createReflectedFixtureRootGeometry(rootGeometry);
    const instance = composeReflectedFixtureInstanceMatrix(
      new THREE.Matrix4(), server,
    );
    assert.equal(fixtureMatrixParity(instance), 1);
    for (const index of new Set([0, Math.floor(position.count / 2), position.count - 1])) {
      const local = new THREE.Vector3().fromBufferAttribute(position, index);
      const expected = local.clone().applyMatrix4(sourceMesh.matrixWorld)
        .applyMatrix4(server);
      const reflectedIndex = reflectedGeometry.getIndex() ? index : reflectedVertexIndex(
        index, position.count,
      );
      const rendered = new THREE.Vector3().fromBufferAttribute(
        reflectedGeometry.getAttribute("position"), reflectedIndex,
      ).applyMatrix4(instance);
      assert.ok(expected.distanceTo(rendered) <= 2.0e-6);
    }
    const expectedBounds = transformAttributeBounds(
      position, [sourceMesh.matrixWorld, server],
    );
    const renderedBounds = transformAttributeBounds(
      reflectedGeometry.getAttribute("position"), [instance],
    );
    assertBoxClose(renderedBounds, expectedBounds, 2.0e-6);
    assert.deepEqual([...position.array], positionsBefore);
    const materialsAfter = Array.isArray(sourceMesh.material)
      ? sourceMesh.material : [sourceMesh.material];
    assert.deepEqual(materialsAfter.map(fixtureMaterialSnapshot), materialValuesBefore);
    materialsAfter.forEach((material, index) => assert.equal(material, materialsBefore[index]));
    rootGeometry.dispose();
    reflectedGeometry.dispose();
    disposeGlbScene(gltf.scene);
  }
});

test("inspection light rig is neutral, camera-relative, shadow-free, and detachable", () => {
  const camera = new THREE.PerspectiveCamera(42, 1, 0.001, 30);
  const rig = createInspectionLightRig(camera);
  assert.equal(rig.ambient.isAmbientLight, true);
  assert.equal(rig.hemisphere.isHemisphereLight, true);
  assert.equal(rig.cameraKey.isDirectionalLight, true);
  assert.equal(rig.ambient.color.getHex(), 0xffffff);
  assert.equal(rig.hemisphere.color.getHex(), 0xffffff);
  assert.equal(rig.cameraKey.color.getHex(), 0xffffff);
  assert.equal(rig.ambient.intensity, 0.18);
  assert.equal(rig.hemisphere.intensity, 0.28);
  assert.equal(rig.cameraKey.intensity, 0.85);
  assert.equal(rig.cameraKey.parent, camera);
  assert.equal(rig.cameraKey.target.parent, camera);
  assert.equal(rig.cameraKey.castShadow, false);
  assert.equal(rig.root.children.length, 2);
  rig.dispose();
  assert.equal(rig.cameraKey.parent, null);
  assert.equal(rig.cameraKey.target.parent, null);
  assert.equal(rig.root.children.length, 0);
  rig.dispose();
});

test("camera bounds union exact room, plant, reference, and transformed fixtures", () => {
  const combined = combineAuthoritativeBounds(scene(), {
    minimum_xyz: [-3, 0.25, -2], maximum_xyz: [4, 2.75, 2.5],
  });
  assert.deepEqual(combined, {
    minimum_xyz: [-3, 0, -2], maximum_xyz: [4, 2.75, 2.5],
  });
});

test("official Three 0.184 loader vendoring stays local and includes its utility", () => {
  const script = readFileSync(
    new URL("../scripts/vendor-three.mjs", import.meta.url), "utf8",
  );
  const html = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/index.html", import.meta.url), "utf8",
  );
  assert.match(script, /expectedVersion = "0\.184\.0"/);
  assert.match(script, /examples\/jsm\/environments\/RoomEnvironment\.js/);
  assert.match(script, /examples\/jsm\/loaders\/GLTFLoader\.js/);
  assert.match(script, /examples\/jsm\/utils\/BufferGeometryUtils\.js/);
  assert.match(script, /examples\/jsm\/utils\/SkeletonUtils\.js/);
  assert.match(html, /"three\/addons\/": "\.\/vendor\/addons\/"/);
  assert.doesNotMatch(`${script}\n${html}`, /https?:\/\/|cdn/i);
});

function transformBoxCorners(box, matrices) {
  const output = new THREE.Box3().makeEmpty();
  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) {
        const corner = new THREE.Vector3(x, y, z);
        for (const matrix of matrices) corner.applyMatrix4(matrix);
        output.expandByPoint(corner);
      }
    }
  }
  return output;
}

function transformAttributeBounds(position, matrices) {
  const output = new THREE.Box3().makeEmpty();
  for (let index = 0; index < position.count; index += 1) {
    const point = new THREE.Vector3().fromBufferAttribute(position, index);
    for (const matrix of matrices) point.applyMatrix4(matrix);
    output.expandByPoint(point);
  }
  return output;
}

function assertBoxClose(actual, expected, tolerance) {
  assert.ok(actual.min.distanceTo(expected.min) <= tolerance, "box minimum differs");
  assert.ok(actual.max.distanceTo(expected.max) <= tolerance, "box maximum differs");
}

function reflectedVertexIndex(index, count) {
  assert.equal(count % 3, 0);
  const triangleStart = index - (index % 3);
  if (index % 3 === 1) return triangleStart + 2;
  if (index % 3 === 2) return triangleStart + 1;
  return index;
}

function fixtureMaterialSnapshot(material) {
  return {
    alphaTest: material.alphaTest,
    color: material.color?.getHex(),
    emissive: material.emissive?.getHex(),
    metalness: material.metalness,
    opacity: material.opacity,
    roughness: material.roughness,
    side: material.side,
    transparent: material.transparent,
  };
}

async function loadActualFixtureGlb(resourcePath) {
  const bytes = readFileSync(new URL(
    `../src/fspm_optics/resources/viewer/${resourcePath}`, import.meta.url,
  ));
  const arrayBuffer = bytes.buffer.slice(
    bytes.byteOffset, bytes.byteOffset + bytes.byteLength,
  );
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((url) => {
    throw new Error(`actual fixture GLB attempted secondary load: ${url}`);
  });
  const loader = new GLTFLoader(manager);
  return new Promise((resolve, reject) => loader.parse(arrayBuffer, "", resolve, reject));
}

function disposeGlbScene(sceneRoot) {
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  sceneRoot.traverse((node) => {
    if (node.geometry) geometries.add(node.geometry);
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (!material) continue;
      materials.add(material);
      for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const texture of textures) texture.dispose();
  for (const material of materials) material.dispose();
}

function leafShaderStub() {
  return {
    uniforms: {},
    vertexShader: "#include <common>\n#include <begin_vertex>",
    fragmentShader: [
      "#include <common>",
      "#include <map_fragment>",
      "#include <color_fragment>",
      "#include <roughnessmap_fragment>",
      "#include <normal_fragment_maps>",
    ].join("\n"),
  };
}

function scientificGlb({
  includeUv = true,
  uvComponentType = 5126,
  uvCount = 3,
  uvMax = [1, 1],
  uvMin = [0, 0],
  uvType = "VEC2",
  uvValues = [0, 0.5, 0.5, 0, 1, 0.5],
} = {}) {
  const blocks = [
    new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]),
    new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
  ];
  if (includeUv) blocks.push(new Float32Array(uvValues));
  blocks.push(new Uint32Array(3), new Uint32Array(3), new Uint32Array(3));
  const offsets = [];
  let byteLength = 0;
  for (const block of blocks) {
    offsets.push(byteLength);
    byteLength += block.byteLength;
  }
  const binary = new Uint8Array(byteLength);
  blocks.forEach((block, index) => binary.set(
    new Uint8Array(block.buffer, block.byteOffset, block.byteLength), offsets[index],
  ));
  const bufferViews = blocks.map((block, index) => ({
    buffer: 0, byteOffset: offsets[index], byteLength: block.byteLength,
  }));
  const accessors = [
    { bufferView: 0, componentType: 5126, count: 3, type: "VEC3" },
    { bufferView: 1, componentType: 5126, count: 3, type: "VEC3" },
  ];
  const attributes = { NORMAL: 1, POSITION: 0 };
  let identityBufferView = 2;
  if (includeUv) {
    accessors.push({
      bufferView: 2,
      componentType: uvComponentType,
      count: uvCount,
      max: uvMax,
      min: uvMin,
      type: uvType,
    });
    attributes.TEXCOORD_0 = 2;
    identityBufferView = 3;
  }
  for (const name of ["_LEAF_INDEX", "_FACE_INDEX", "_PATCH_INDEX"]) {
    attributes[name] = accessors.length;
    accessors.push({
      bufferView: identityBufferView,
      componentType: 5125,
      count: 3,
      type: "SCALAR",
    });
    identityBufferView += 1;
  }
  const document = {
    asset: { version: "2.0" },
    buffers: [{ byteLength }],
    bufferViews,
    accessors,
    materials: [{ doubleSided: true }],
    meshes: [{ primitives: [{ attributes, material: 0, mode: 4 }] }],
    nodes: [{ mesh: 0 }],
    scenes: [{ nodes: [0] }],
    scene: 0,
  };
  const encoded = new TextEncoder().encode(JSON.stringify(document));
  const jsonLength = encoded.length + ((-encoded.length) & 3);
  const totalLength = 12 + 8 + jsonLength + 8 + binary.byteLength;
  const bytes = new ArrayBuffer(totalLength);
  const view = new DataView(bytes);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, totalLength, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(bytes, 20, encoded.length).set(encoded);
  new Uint8Array(bytes, 20 + encoded.length, jsonLength - encoded.length).fill(0x20);
  const binaryHeader = 20 + jsonLength;
  view.setUint32(binaryHeader, binary.byteLength, true);
  view.setUint32(binaryHeader + 4, 0x004e4942, true);
  new Uint8Array(bytes, binaryHeader + 8).set(binary);
  return bytes;
}

function scene() {
  const lengthM = 3.81;
  const widthM = 2.3622;
  const minX = -lengthM / 2;
  const maxX = lengthM / 2;
  const minZ = -widthM / 2;
  const maxZ = widthM / 2;
  return {
    schema_id: "fspm-optics.juvenile-rex-run-scene",
    schema_version: 4,
    viewer_resource_version: VIEWER_RESOURCE_VERSION,
    run: { run_id: "a".repeat(32), system_id: "conventional" },
    fixtures: {
      catalog: "fixtures/catalog.v1.json",
      catalog_byte_length: 4096,
      catalog_sha256: "f".repeat(64),
      authoritative_layout_sha256: "8".repeat(64),
      fixture_plan_sha256: "9".repeat(64),
      mounting_height_sha256: "5".repeat(64),
      fixture_count: 6,
      asset_group_count: 1,
    },
    requested_room: {
      length_ft: 12.5, length_m: lengthM, width_ft: 7.75, width_m: widthM,
    },
    aligned_simulation_room: {
      length_m: lengthM,
      width_m: widthM,
      long_axis: "x",
      coordinate_frame: {
        policy_id: "requested_room_to_long_axis_x_rigid_rotation_v1",
        requested_room_m: { length_x_m: lengthM, width_y_m: widthM },
        aligned_simulation_room_m: { length_x_m: lengthM, width_y_m: widthM },
        axes_swapped: false,
        rotation_degrees_about_z: 0,
        rotation_matrix_row_major: [1, 0, 0, 0, 1, 0, 0, 0, 1],
        determinant: 1,
        translation_m: [0, 0, 0],
        position_rule: "rotation_then_translation",
        direction_rule: "rotation_only",
        preserves_z: true,
      },
    },
    profile: {
      profile_id: PROFILE_ID,
      sampling_profile_id: OPTIMIZED_SAMPLING_PROFILE,
      manifest: `profiles/${PROFILE_ID}/profile.v1.json`,
      manifest_sha256: "a".repeat(64),
    },
    natural_fit: {
      artifact_role: "natural_fit_layout", artifact_sha256: "b".repeat(64),
      ordering: "Y-major/X-minor", plan_hash: "c".repeat(64), plant_count: 6,
      policy_id: "natural_fit_0p40m_sampling_v1", profile_id: PROFILE_ID,
    },
    plant_instances: {
      plant_ids: Array.from({ length: 6 }, (_, index) => `plant-${index}`),
      instance_translations: {
        filename: "instances.f32le.bin", record_layout: "translation_x_y_z",
        coordinate_space: "viewer right-handed meters, Y-up",
        scientific_to_viewer: "(x, y, z) -> (x, z, -y)",
        component_type: "float32", byte_order: "little-endian", stride_bytes: 12,
        count: 6, byte_length: 72, sha256: "d".repeat(64),
      },
    },
    camera_bounds: { minimum_xyz: [minX, 0, minZ], maximum_xyz: [maxX, 1, maxZ] },
    plant_bounds: { minimum_xyz: [-1, 0, -1], maximum_xyz: [1, 1, 1] },
    projected_footprint_bounds: { minimum_xz: [-1, -1], maximum_xz: [1, 1] },
    room_bounds: { minimum_xz: [minX, minZ], maximum_xz: [maxX, maxZ] },
    reference_plane: {
      coordinate_system: "viewer Y-up",
      vertices_xyz: [[minX, 0, minZ], [maxX, 0, minZ],
        [maxX, 0, maxZ], [minX, 0, maxZ]],
    },
    mounting_height: mountingHeight(),
    ppfd_heatmap: ppfdHeatmapSource(),
  };
}

function sceneForRoom(lengthFt, widthFt, systemId) {
  const value = scene();
  const lengthM = lengthFt * 0.3048;
  const widthM = widthFt * 0.3048;
  const axesSwapped = widthM > lengthM;
  const alignedLengthM = axesSwapped ? widthM : lengthM;
  const alignedWidthM = axesSwapped ? lengthM : widthM;
  const frame = value.aligned_simulation_room.coordinate_frame;
  value.run.system_id = systemId;
  value.requested_room = {
    length_ft: lengthFt, length_m: lengthM,
    width_ft: widthFt, width_m: widthM,
  };
  value.aligned_simulation_room = {
    length_m: alignedLengthM,
    width_m: alignedWidthM,
    long_axis: "x",
    coordinate_frame: {
      ...frame,
      requested_room_m: { length_x_m: lengthM, width_y_m: widthM },
      aligned_simulation_room_m: {
        length_x_m: alignedLengthM, width_y_m: alignedWidthM,
      },
      axes_swapped: axesSwapped,
      rotation_degrees_about_z: axesSwapped ? -90 : 0,
      rotation_matrix_row_major: axesSwapped
        ? [0, 1, 0, -1, 0, 0, 0, 0, 1]
        : [1, 0, 0, 0, 1, 0, 0, 0, 1],
    },
  };
  const minX = -alignedLengthM / 2;
  const maxX = alignedLengthM / 2;
  const minZ = -alignedWidthM / 2;
  const maxZ = alignedWidthM / 2;
  value.room_bounds = { minimum_xz: [minX, minZ], maximum_xz: [maxX, maxZ] };
  value.camera_bounds = {
    minimum_xyz: [minX, 0, minZ], maximum_xyz: [maxX, 1, maxZ],
  };
  value.reference_plane.vertices_xyz = [
    [minX, 0, minZ], [maxX, 0, minZ],
    [maxX, 0, maxZ], [minX, 0, maxZ],
  ];
  return value;
}

function renderPpfdColorRegressionSvg() {
  const displayScale = {
    minimum_ppfd_umol_m2_s: 800,
    maximum_ppfd_umol_m2_s: 1200,
  };
  const panels = [
    {
      label: "Proposed · 800–1200 fixed",
      values: [
        999.4, 1000.1, 999.8, 1000.5,
        1000.2, 999.7, 1000.3, 999.9,
        999.6, 1000.4, 1000.0, 999.8,
        1000.1, 999.9, 1000.2, 999.7,
      ],
    },
    {
      label: "Conventional · 800–1200 fixed",
      values: [
        800, 840, 900, 960,
        860, 940, 1020, 1100,
        920, 1000, 1080, 1160,
        980, 1060, 1140, 1200,
      ],
    },
  ];
  const lines = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="230" viewBox="0 0 420 230">',
    '  <rect width="420" height="230" fill="#101612"/>',
    '  <style>text{font:11px monospace;fill:#e5ece6}.scale{fill:#aab9ad}</style>',
  ];
  panels.forEach((panel, panelIndex) => {
    const panelX = 20 + panelIndex * 205;
    lines.push(`  <text x="${panelX}" y="20">${panel.label}</text>`);
    panel.values.forEach((value, index) => {
      const [red, green, blue] = ppfdSrgbAtDisplayScale(value, displayScale);
      const x = panelX + (index % 4) * 40;
      const y = 34 + Math.floor(index / 4) * 40;
      lines.push(
        `  <rect x="${x}" y="${y}" width="40" height="40" `
        + `fill="rgb(${red} ${green} ${blue})" fill-opacity="0.7"/>`,
      );
    });
    lines.push(`  <text class="scale" x="${panelX}" y="210">800</text>`);
    lines.push(`  <text class="scale" x="${panelX + 136}" y="210">1200</text>`);
  });
  lines.push("</svg>");
  return `${lines.join("\n")}\n`;
}

function ppfdHeatmapSource() {
  const source = {
    availability: "available",
    metadata: {
      filename: "ppfd-heatmap/visualization.json",
      sha256: "1".repeat(64),
      byte_length: 2048,
    },
    scalar_field: {
      filename: "ppfd-heatmap/ppfd-scatter.f32le.bin",
      sha256: "2".repeat(64),
      byte_length: 48,
      component_type: "float32",
      byte_order: "little-endian",
      record_layout: "x_m,y_m,ppfd_umol_m2_s",
      stride_bytes: 12,
      count: 4,
    },
    grid: {
      width: 2,
      height: 2,
      x_centers_m: [-1, 1],
      y_centers_m: [-2, 2],
      cell_edge_bounds_m: {
        x_min: -2, x_max: 2, y_min: -4, y_max: 4,
      },
      reference_plane_z_m: 0.005,
      coordinate_system: "right-handed scientific XY, meters, Z-up",
      scientific_to_viewer: "(x, y, z) -> (x, z, -y)",
    },
    scalar_units: "micromole_per_square_meter_per_second",
    display_scale: {
      minimum_ppfd_umol_m2_s: 800,
      maximum_ppfd_umol_m2_s: 1200,
      colormap: COLORMAP_ID,
    },
    source_field_identity_sha256: "3".repeat(64),
    interpolation_policy_id: INTERPOLATION_POLICY_ID,
  };
  source.target_coverage = targetCoverageContract(source);
  return source;
}

function targetCoverageContract(source) {
  return {
    schema_id: "fspm-optics.viewer-target-coverage",
    schema_version: 1,
    availability: "available",
    unavailable_reason_code: null,
    target_classification_basis: TARGET_CLASSIFICATION_BASIS,
    target_classification_source: TARGET_CLASSIFICATION_SOURCE,
    scientific_limitation: TARGET_COVERAGE_LIMITATION,
    system_id: "conventional",
    reference: {
      ppfd_umol_m2_s: 1000,
      source: "requested_lighting_target",
      policy_mode: "automatic",
    },
    tolerance_ppfd_umol_m2_s: 20,
    deviation_formula: "(coverage_ppfd - reference_ppfd) / tolerance_ppfd",
    target_band_deviation: { minimum: -1, maximum: 1, bounds: "inclusive" },
    palette: {
      palette_id: "target-coverage-deviation-8-anchor-v1",
      continuous_interpolation: true,
      clamp_below_deviation: -4,
      clamp_above_deviation: 6,
      anchors: structuredClone(TARGET_COVERAGE_PALETTE),
    },
    sampling: {
      interpolation_policy_id: "stage_a_regular_grid_bilinear_half_cell_clamp_v1",
      method: "manual_four_cell_bilinear",
      supported_extent: "sample-center rectangle expanded by half a grid step",
      edge_band_behavior: "clamp to nearest center",
      outside_behavior: "target coverage unavailable",
      support_bounds_m: { ...source.grid.cell_edge_bounds_m, bounds: "inclusive" },
      coordinate_transform_policy_id:
        "requested_room_to_long_axis_x_rigid_rotation_v1",
      axes_swapped_from_requested_room: false,
      field_sampling_mapping: { x: "aligned_x", y: "aligned_y" },
    },
    canonical_leaf_geometry: {
      centroid_policy_id: "one_sided_triangle_area_weighted_leaf_centroid_v1",
      leaf_ordering: "canonical-leaf order",
      displayed_leaf_ordering: "plant-major; canonical-leaf order",
      canonical_leaf_count: 12,
      canonical_leaf_ids: Array.from({ length: 12 }, (_, index) => `leaf-${index}`),
      representative_centroids_simulation_xy_m: Array.from(
        { length: 12 }, () => [0, 0],
      ),
      canonical_topology_sha256: "6".repeat(64),
      leaf_geometry_identity_sha256: "7".repeat(64),
      instance_translation_source: "scene.plant_instances.instance_translations",
    },
    parent_source_field_identity_sha256: source.source_field_identity_sha256,
  };
}

function ppfdHeatmapMetadata(current, source) {
  return {
    annotations: {},
    artifacts: {
      "ppfd-scatter.f32le.bin": {
        sha256: source.scalar_field.sha256,
        byte_length: source.scalar_field.byte_length,
      },
    },
    display: {
      color_limits_ppfd_umol_m2_s: [
        source.display_scale.minimum_ppfd_umol_m2_s,
        source.display_scale.maximum_ppfd_umol_m2_s,
      ],
      colormap: {
        name: COLORMAP_ID,
        anchors_srgb_8bit: [
          [68, 1, 84],
          [70, 50, 126],
          [54, 92, 141],
          [39, 127, 142],
          [31, 161, 135],
          [74, 193, 109],
          [160, 218, 57],
          [253, 231, 37],
        ],
      },
    },
    field: {
      identity_sha256: source.source_field_identity_sha256,
      sample_count: source.scalar_field.count,
      coordinate_system: source.grid.coordinate_system,
      reference_plane_z_m: source.grid.reference_plane_z_m,
      value_units: source.scalar_units,
    },
    grid: {
      kind: "regular",
      resolution: { x: source.grid.width, y: source.grid.height },
      x_centers_m: source.grid.x_centers_m,
      y_centers_m: source.grid.y_centers_m,
      extent_m: source.grid.cell_edge_bounds_m,
      reshape_order: "rows_ascending_y_columns_ascending_x",
      image_origin: "lower",
      interpolation: {
        applied: false,
        method: null,
        resolution: null,
        fill_behavior: null,
        original_sample_identity_sha256: source.source_field_identity_sha256,
      },
    },
    orientation: {
      sample_coordinates_transformed: false,
      positive_y_is_image_up: true,
      layout_axes_swapped_from_requested_room: false,
    },
    overlay: {},
    run_id: current.run.run_id,
    scatter: {
      filename: "ppfd-scatter.f32le.bin",
      sha256: source.scalar_field.sha256,
      byte_length: source.scalar_field.byte_length,
      byte_order: source.scalar_field.byte_order,
      component_type: source.scalar_field.component_type,
      record_layout: source.scalar_field.record_layout,
      stride_bytes: source.scalar_field.stride_bytes,
      count: source.scalar_field.count,
      source_field_identity_sha256: source.source_field_identity_sha256,
    },
    schema_id: "fspm-optics.ppfd-visualization",
    schema_version: 1,
    transforms: {
      physics_recomputed: false,
      target_rescaling: false,
      symmetrization: false,
      smoothing: false,
      clipping: false,
      correction: false,
      hidden_normalization: false,
      sample_exclusion: false,
    },
  };
}

function ppfdScatterBuffer(records, littleEndian = true) {
  const rows = records ?? [
    [1, 2, 40],
    [-1, -2, 10],
    [1, -2, 20],
    [-1, 2, 30],
  ];
  const bytes = new ArrayBuffer(rows.length * 12);
  const view = new DataView(bytes);
  rows.forEach((row, record) => row.forEach((value, component) => {
    view.setFloat32(record * 12 + component * 4, value, littleEndian);
  }));
  return bytes;
}

function fixtureCatalog() {
  const assetId = "conventional-led-8-bar-v1";
  const approved = APPROVED_FIXTURE_ASSETS[assetId];
  const fixtureIds = Array.from({ length: 6 }, (_, index) => `fixture-${index}`);
  const fixturePlan = {
    run_id: "a".repeat(32), system_id: "conventional",
    requested_room_ft: { length: 12.5, width: 7.75 },
    authoritative_layout_sha256: "8".repeat(64),
    mounting_height_sha256: "5".repeat(64),
    ordering: "authoritative fixture order", placement_inference: false,
    display_classification_policy: "system_fixed_display_asset_v1",
    fixtures: fixtureIds.map((fixtureId) => ({
      fixture_id: fixtureId,
      display_asset_id: assetId,
      display_fixture_type: "conventional_led_8_bar",
    })),
  };
  return {
    schema_id: "fspm-optics.run-fixture-catalog", schema_version: 2,
    run: { run_id: "a".repeat(32), system_id: "conventional" },
    requested_room_ft: { length: 12.5, width: 7.75 },
    authoritative_layout_sha256: "8".repeat(64),
    fixture_plan_sha256: "9".repeat(64),
    ordering: "authoritative fixture order within each stable asset group",
    transform_authority: "server_resolved_from_authoritative_layout_and_immutable_asset_registry",
    browser_transform_inference: false, fixture_count: 6, asset_group_count: 1,
    mounting_height: mountingHeight(),
    fixture_plan: fixturePlan,
    asset_groups: [{
      display_asset_id: assetId,
      display_fixture_type: "conventional_led_8_bar",
      asset: {
        filename: `assets/${assetId}-${approved.sha256.slice(0, 16)}.glb`,
        byte_size: approved.byteSize, sha256: approved.sha256,
      },
      registry: {
        asset_id: assetId, approved_system_id: approved.systemId,
        approved_display_fixture_type: approved.displayFixtureType,
        packaged_resource_path: approved.resourcePath,
        expected_byte_size: approved.byteSize, expected_sha256: approved.sha256,
        meters_per_asset_unit: 0.001,
        dimension_correction_scale_xyz: [1, 1, 1],
        asset_local_axes: {
          up: "positive_y_after_authored_glb_scene_transform",
          forward: "negative_z_after_authored_glb_scene_transform",
        },
        pivot: { contract: "test" },
        material_alpha_modes: ["OPAQUE"],
        approved_base_color_alpha_factors: [1],
        materials_double_sided: true,
        approved_glb_extensions: ["KHR_mesh_quantization"],
      },
      ordered_fixture_ids: fixtureIds,
      instance_matrices: {
        filename: `transforms/${assetId}-${"9".repeat(16)}.f32le.bin`,
        component_type: "float32", byte_order: "little-endian", count: 6,
        stride_bytes: 64, byte_length: 384,
        record_layout: "matrix4_column_major",
        matrix_convention: "column vectors; viewer_position = matrix * packaged_glb_scene_position",
        coordinate_space: "viewer right-handed meters, Y-up",
        scientific_to_viewer: "(x, y, z) -> (x, z, -y)",
        sha256: "6".repeat(64),
      },
    }],
  };
}

function alignmentLattice(moduleCount) {
  const z = 0.4970000008899718;
  const links = Array.from({ length: moduleCount - 1 }, (_, index) => ({
    link_index: index,
    kind: "horizontal",
    start_module_index: index,
    end_module_index: index + 1,
    start_xyz_m: [index + 0.075, 0, z],
    end_xyz_m: [index + 0.925, 0, z],
  }));
  return {
    schema_id: "fspm-optics.proposed-standalone-alignment-lattice",
    schema_version: 1,
    topology_id: "standalone_module_integer_staggered_triangular_graph_v1",
    geometry_id: "straight_taut_free_span_cylinders_v1",
    attachment_policy: {
      id: "proposed_led_module_authenticated_rear_plane_v1",
      authenticated_asset_id: "proposed-led-module-v1",
      authenticated_glb_sha256: (
        "f910a7fc512635bcb3a48d7b546175ef13623135836a76eeb625b2ca12cd5445"
      ),
      surface: "rear_most_authenticated_full_glb_plane",
      rear_plane_offset_from_aperture_m: 0.034800000889971816,
      module_envelope_m: [0.15, 0.15],
      endpoint_contact_only: true,
    },
    diameter_m: 0.0015875,
    radius_m: 0.00079375,
    material_id: "fixture_body_anodized_aluminum_v2",
    transport_role: "non_emitting_occluding_geometry",
    module_count: moduleCount,
    counts: { total: links.length, horizontal: links.length, diagonal: 0 },
    links,
    identity_sha256: "4".repeat(64),
  };
}

function mountingHeight(heightIn = 18) {
  const canonical = new Map([
    [18, { mountingHeightM: 0.4572, apertureZM: 0.4622 }],
    [24, { mountingHeightM: 0.6096, apertureZM: 0.6146 }],
  ]).get(heightIn);
  if (!canonical) throw new Error("Unsupported mounting-height test fixture.");
  return {
    schema_id: "fspm-optics.mounting-height-provenance",
    schema_version: 1,
    mounting_height_in: heightIn,
    input_unit: "inch",
    meters_per_inch: 0.0254,
    definition: "emitting_aperture_plane_to_receiver_reference_plane",
    reference_plane_z_m: 0.005,
    mounting_height_m: canonical.mountingHeightM,
    emitting_aperture_plane_z_m: canonical.apertureZM,
    room_ceiling_z_m: 3.048,
  };
}

function fixtureGlb(document) {
  const encoded = new TextEncoder().encode(JSON.stringify(document));
  const jsonLength = encoded.length + ((-encoded.length) & 3);
  const totalLength = 12 + 8 + jsonLength + 8 + 4;
  const bytes = new ArrayBuffer(totalLength);
  const view = new DataView(bytes);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, totalLength, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(bytes, 20, encoded.length).set(encoded);
  new Uint8Array(bytes, 20 + encoded.length, jsonLength - encoded.length).fill(0x20);
  const binaryHeader = 20 + jsonLength;
  view.setUint32(binaryHeader, 4, true);
  view.setUint32(binaryHeader + 4, 0x004e4942, true);
  return bytes;
}

function profile() {
  const artifact = (filename) => ({ filename, byte_size: 4, sha256: "e".repeat(64) });
  return {
    schema_id: "fspm-optics.juvenile-rex-viewer-profile", schema_version: 3,
    profile_id: PROFILE_ID,
    sampling_profile_id: OPTIMIZED_SAMPLING_PROFILE,
    sampling_calibration_status: "uncalibrated_for_phase27g_d2_surface_flux_display",
    development: { approximate_days_after_transplant: 9, phenological_boundary: "BBCH 19/pre-41" },
    calibration_status: "literature_constrained_procedural_reference_geometry",
    model_scope: {
      growth_response_model: false, measured_reconstruction: false,
      procedural_reference_geometry: true,
    },
    counts: { faces: 1920, leaves: 12, patches: 192, receivers: 384 },
    units: "meters", scientific_coordinate_system: "right-handed, meters, z-up",
    viewer_coordinate_system: "right-handed, meters, Y-up",
    coordinate_transform: {
      formula: "(x, y, z) -> (x, z, -y)",
      matrix_row_major: [1, 0, 0, 0, 0, 1, 0, -1, 0],
      proper_rotation: true, winding_reversed: false,
    },
    artifacts: {
      geometry: artifact("geometry.glb"), identity_map: artifact("identity-map.v1.json"),
      receivers: artifact("receivers.f32le.bin"),
    },
  };
}

function identity() {
  return {
    schema_id: "fspm-optics.juvenile-rex-identity-map", schema_version: 2,
    profile_id: PROFILE_ID, sampling_profile_id: OPTIMIZED_SAMPLING_PROFILE,
    plant_ids: ["plant-0"],
    leaf_ids: Array.from({ length: 12 }, (_, index) => `leaf-${index}`),
    face_ids: Array.from({ length: 1920 }, (_, index) => `face-${index}`),
    patch_ids: Array.from({ length: 192 }, (_, index) => `patch-${index}`),
    receiver_ids: Array.from({ length: 384 }, (_, index) => `receiver-${index}`),
    face_to_leaf: Array.from({ length: 1920 }, () => 0),
    face_to_patch: Array.from({ length: 1920 }, () => 0),
    patch_to_leaf: Array.from({ length: 192 }, () => 0),
    receiver_to_patch: Array.from({ length: 384 }, (_, index) => Math.floor(index / 2)),
    receiver_side: Array.from(
      { length: 384 }, (_, index) => (index % 2 === 0 ? "front" : "back"),
    ),
  };
}

function legacyScene() {
  const value = optimizedHistoricalScene();
  value.schema_version = 1;
  value.viewer_resource_version = LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION;
  delete value.profile.sampling_profile_id;
  return value;
}

function legacySchemaV2Scene() {
  const value = optimizedHistoricalScene();
  value.viewer_resource_version = D3_VIEWER_RESOURCE_VERSION;
  value.profile.sampling_profile_id = LEGACY_SAMPLING_PROFILE;
  return value;
}

function optimizedHistoricalScene() {
  const value = scene();
  value.schema_version = 2;
  value.viewer_resource_version = D4_VIEWER_RESOURCE_VERSION;
  delete value.mounting_height;
  delete value.ppfd_heatmap;
  delete value.fixtures.mounting_height_sha256;
  return value;
}

function mountingHistoricalScene() {
  const value = scene();
  value.schema_version = 3;
  value.viewer_resource_version = MOUNTING_VIEWER_RESOURCE_VERSION;
  delete value.ppfd_heatmap;
  return value;
}

function legacyIdentity() {
  const value = identity();
  value.schema_version = 1;
  delete value.sampling_profile_id;
  return value;
}

function legacyProfile() {
  const value = profile();
  value.schema_version = 1;
  delete value.sampling_profile_id;
  delete value.sampling_calibration_status;
  return value;
}
