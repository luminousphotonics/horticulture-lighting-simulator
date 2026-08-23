import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import * as THREE from "three";

import { createPlantSurface, applyInstanceTranslations } from "../src/fspm_optics/resources/viewer/renderer.js";
import {
  LEGACY_SAMPLING_PROFILE,
  LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION,
  OPTIMIZED_SAMPLING_PROFILE,
  VIEWER_RESOURCE_VERSION,
  validateSurfaceFluxSceneReference,
} from "../src/fspm_optics/resources/viewer/artifacts.js";
import {
  BACK_ANCHORS,
  BACK_PALETTE,
  FRONT_ANCHORS,
  FRONT_LOCAL_PATCH_ANCHORS,
  FRONT_LOCAL_PATCH_PALETTE,
  MAX_DISPLAY_PLANT_COUNT,
  SCHEMA_V3_BACK_ANCHORS,
  SCHEMA_V3_BACK_PALETTE,
  SCHEMA_V3_BACK_PALETTE_ID,
  SURFACE_FLUX_PALETTE,
  authenticateSurfaceFluxMetadata,
  clearSurfaceFluxColoring,
  createSurfaceFluxColorController,
  deriveAllSurfaceDisplayValues,
  globalPatchIndex,
  loadValidatedSurfaceFluxArtifacts,
  parseSurfaceFluxValues,
  parseDisplayCalibrationCoefficients,
  qualityFamilyFor,
  resolveSurfaceSide,
  validateSurfaceFluxMetadata,
} from "../src/fspm_optics/resources/viewer/surface-flux.js";

globalThis.crypto ??= webcrypto;

const PROFILE_ID = "rex_juvenile_preheading_12leaf_v1";
const HASH = "a".repeat(64);
const CALIBRATION_RESOURCE = JSON.parse(readFileSync(new URL(
  "../src/fspm_optics/resources/calibration/"
    + "surface-flux-front-local-patch-calibration.v1.json",
  import.meta.url,
), "utf8"));

test("schema-v3 backside palette is exact, fixed, and historically additive", () => {
  assert.equal(
    SCHEMA_V3_BACK_PALETTE_ID,
    "surface-flux-back-log-compressed-blue-to-red-8-v2",
  );
  assert.deepEqual(
    SCHEMA_V3_BACK_ANCHORS,
    [0, 0.4, 0.7, 0.9, 1, 10, 100, 512],
  );
  assert.deepEqual(SCHEMA_V3_BACK_PALETTE, [
    { name: "blue", srgb_hex: "#2563EB" },
    { name: "cyan", srgb_hex: "#06B6D4" },
    { name: "teal", srgb_hex: "#14B8A6" },
    { name: "green_low", srgb_hex: "#22C55E" },
    { name: "green_reference", srgb_hex: "#22C55E" },
    { name: "yellow_green", srgb_hex: "#A3E635" },
    { name: "orange", srgb_hex: "#F59E0B" },
    { name: "red", srgb_hex: "#DC2626" },
  ]);
  assert.equal(SCHEMA_V3_BACK_ANCHORS[4], 1);
  assert.equal(SCHEMA_V3_BACK_PALETTE[4].srgb_hex, "#22C55E");
  assert.ok(501.0217 <= SCHEMA_V3_BACK_ANCHORS.at(-1));
  assert.ok(501.6332 <= SCHEMA_V3_BACK_ANCHORS.at(-1));

  assert.deepEqual(
    FRONT_LOCAL_PATCH_ANCHORS,
    [0, 0.59, 0.81, 0.89, 1, 1.14, 1.52, 2.84],
  );
  assert.deepEqual(
    BACK_ANCHORS,
    [0, 0.025, 0.05, 0.14, 0.42, 1, 2.75, 5.33],
  );
  assert.equal(SURFACE_FLUX_PALETTE, BACK_PALETTE);

  const implementation = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/surface-flux.js", import.meta.url),
    "utf8",
  );
  const v3Start = implementation.indexOf("function validatePalettesV3");
  const v3End = implementation.indexOf("function validateTopology", v3Start);
  const v3Validator = implementation.slice(v3Start, v3End);
  assert.match(v3Validator, /back\.palette_id !== SCHEMA_V3_BACK_PALETTE_ID/);
  assert.match(v3Validator, /back\.per_run_extrema_normalization !== false/);
  assert.match(v3Validator,
    /SCHEMA_V3_BACK_ANCHORS, SCHEMA_V3_BACK_PALETTE, "u"/);
  const legendsStart = implementation.indexOf("function validateLegendsV3");
  const legendsEnd = implementation.indexOf("function validLegendCommon", legendsStart);
  const legendValidator = implementation.slice(legendsStart, legendsEnd);
  assert.match(legendValidator, /for \(const metric of METRICS\)/);
  assert.match(legendValidator, /SCHEMA_V3_BACK_ANCHORS/);
  assert.match(legendValidator, /legend\.all_cells_available !== true/);
  assert.match(legendValidator, /legend\.availability_mask_applied !== false/);
  assert.match(implementation, /bool frontSide = gl_FrontFacing;/);
  assert.match(implementation,
    /frontSide \? vSurfaceFluxQ\.r : vSurfaceFluxQ\.g/);
  assert.match(implementation,
    /frontSide \? vSurfaceFluxQ\.b : vSurfaceFluxQ\.a/);
});

test("schema-v3 Float64 blocks explicitly remap into raw FI BI FA BA channels", () => {
  const familyOrder = ["standard", "quality"];
  const blockOrder = [
    "front_incident", "front_absorbed", "back_incident", "back_absorbed",
  ];
  const bytes = new ArrayBuffer(1536 * 8);
  const view = new DataView(bytes);
  let offset = 0;
  for (let family = 0; family < 2; family += 1) {
    for (let block = 0; block < 4; block += 1) {
      for (let patch = 0; patch < 192; patch += 1) {
        view.setFloat64(offset * 8, 1 + family * 4 + block, true);
        offset += 1;
      }
    }
  }
  const artifact = {
    filename: "surface-flux/display-calibration-coefficients.v1.f64le.bin",
    resource_id: "phase27g-d5-c3-authenticated-surface-flux-display-calibration-v1",
    component_type: "float64", byte_order: "little-endian", stride_bytes: 8,
    coefficient_count: 1536, byte_length: 12288, values_per_block: 192,
    family_order: familyOrder, block_order: blockOrder, sha256: HASH,
  };
  const coefficients = parseDisplayCalibrationCoefficients(bytes, artifact);
  const raw = new Float32Array(192 * 4);
  for (let patch = 0; patch < 192; patch += 1) {
    raw.set([10, 20, 30, 40], patch * 4);
  }
  const contract = {
    schemaVersion: 3,
    coefficients,
    family: { coefficientFamily: "standard" },
    reference: { value: 10 },
  };
  const display = deriveAllSurfaceDisplayValues(raw, 1, contract);
  for (const systemId of ["proposed", "conventional", "hps"]) {
    const systemDisplay = deriveAllSurfaceDisplayValues(
      raw, 1, { ...contract, systemId },
    );
    assert.deepEqual(Array.from(systemDisplay), Array.from(display));
  }
  for (const values of [display.slice(0, 4), display.slice(-4)]) {
    assert.equal(values[0], 1);
    assert.ok(Math.abs(values[1] - 20 / 30) < 1e-6);
    assert.equal(values[2], 1.5);
    assert.equal(values[3], 1);
  }
  assert.deepEqual(Array.from(raw.slice(0, 4)), [10, 20, 30, 40]);
  assert.equal(coefficients.standard.front_incident[0], 1);
  assert.equal(coefficients.standard.front_absorbed[0], 2);
  assert.equal(coefficients.standard.back_incident[0], 3);
  assert.equal(coefficients.standard.back_absorbed[0], 4);
  assert.equal(Object.isFrozen(coefficients.standard.front_incident), true);
  assert.throws(() => {
    coefficients.standard.front_incident[0] = 99;
  }, TypeError);
});

test("schema-v3 coefficient failure rejects the whole normalization payload", () => {
  const bytes = new ArrayBuffer(1536 * 8);
  const view = new DataView(bytes);
  for (let index = 0; index < 1536; index += 1) view.setFloat64(index * 8, 1, true);
  view.setFloat64(777 * 8, 0, true);
  assert.throws(() => parseDisplayCalibrationCoefficients(bytes, {
    component_type: "float64", byte_order: "little-endian", stride_bytes: 8,
    coefficient_count: 1536, byte_length: 12288,
    family_order: ["standard", "quality"],
    block_order: [
      "front_incident", "front_absorbed", "back_incident", "back_absorbed",
    ],
  }), /non-finite or nonpositive/);
});

test("failed schema-v3 promotion clears stale coloring and the entire new payload", () => {
  const surface = plantSurface(2);
  const historical = fixture("standard");
  const staleValues = new Float32Array(2 * 192 * 4).fill(1);
  const stale = createSurfaceFluxColorController(
    surface, { patchCount: 384, values: staleValues }, historical.metadata,
  );
  const incoming = new Float32Array(2 * 192 * 4).fill(2);
  const metadata = {
    schema_version: 3,
    topology: { counts: { plants: 2 } },
  };
  assert.throws(
    () => createSurfaceFluxColorController(
      surface, { patchCount: 384, rawValues: incoming }, metadata, {},
    ),
    /metadata identity|authenticated/,
  );
  assert.equal(stale.getState().disposed, true);
  assert.equal(staleValues.every((value) => value === 0), true);
  assert.equal(incoming.every((value) => value === 0), true);
  assert.equal(surface.userData.surfaceFluxController, undefined);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("optimized scenes accept only additive metadata v3 and preserve legacy routes", () => {
  const scene = {
    schema_id: "fspm-optics.juvenile-rex-run-scene",
    schema_version: 4,
    viewer_resource_version: VIEWER_RESOURCE_VERSION,
    profile: { sampling_profile_id: OPTIMIZED_SAMPLING_PROFILE },
    surface_flux: {
      availability: "available",
      metadata: {
        filename: "surface-flux/metadata.v3.json", byte_length: 10, sha256: HASH,
      },
    },
  };
  assert.equal(validateSurfaceFluxSceneReference(scene), scene.surface_flux.metadata);
  scene.surface_flux.metadata.filename = "surface-flux/metadata.v2.json";
  assert.throws(
    () => validateSurfaceFluxSceneReference(scene),
    /Scene surface-flux reference is incompatible/,
  );
  scene.profile.sampling_profile_id = LEGACY_SAMPLING_PROFILE;
  assert.equal(validateSurfaceFluxSceneReference(scene), scene.surface_flux.metadata);
});

test("four quality modes select only the declared two calibration families", () => {
  const expected = {
    direct: ["neutral-upper-hemisphere-standard-v1", "standard", true],
    standard: ["neutral-upper-hemisphere-standard-v1", "standard", false],
    quality: ["neutral-upper-hemisphere-quality-v1", "quality", false],
    rigorous: ["neutral-upper-hemisphere-quality-v1", "quality", true],
  };
  for (const [quality, [profileId, sourceQuality, proxy]] of Object.entries(expected)) {
    const selected = qualityFamilyFor(quality);
    assert.equal(selected.profile.profile_id, profileId);
    assert.equal(selected.profile.calibration_source_quality, sourceQuality);
    assert.equal(selected.proxy, proxy);
  }
  assert.equal(qualityFamilyFor("instant"), null);
  assert.equal(qualityFamilyFor(undefined), null);
});

test("schema v1 retains its fixed scalar anchors and shared palette", () => {
  for (const quality of ["direct", "standard", "quality", "rigorous"]) {
    const contract = fixture(quality);
    assert.equal(validateSurfaceFluxMetadata(contract.metadata, contract.context).family.proxy,
      quality === "direct" || quality === "rigorous");
    assert.deepEqual(contract.metadata.palette.front_anchors_z, FRONT_ANCHORS);
    assert.deepEqual(contract.metadata.palette.back_anchors_z, BACK_ANCHORS);
    assert.deepEqual(contract.metadata.palette.colors, SURFACE_FLUX_PALETTE);
  }
});

test("historical schema-v1 sampling is inferred only for authenticated D2 hashes", () => {
  const contract = fixture("standard");
  delete contract.context.scene.profile.sampling_profile_id;
  delete contract.context.profile.sampling_profile_id;
  delete contract.context.identity.sampling_profile_id;
  assert.equal(contract.context.scene.profile.sampling_profile_id, undefined);
  assert.equal(validateSurfaceFluxMetadata(contract.metadata, contract.context).schemaVersion, 1);
  contract.metadata.topology.receivers_sha256 = "0".repeat(64);
  assert.throws(
    () => validateSurfaceFluxMetadata(contract.metadata, contract.context),
    /calibration does not cover/,
  );
});

test("optimized sampling cannot access historical D2 calibration", () => {
  const contract = fixtureV2("standard");
  contract.context.scene.profile.sampling_profile_id = OPTIMIZED_SAMPLING_PROFILE;
  contract.context.profile.sampling_profile_id = OPTIMIZED_SAMPLING_PROFILE;
  contract.context.identity.sampling_profile_id = OPTIMIZED_SAMPLING_PROFILE;
  assert.throws(
    () => validateSurfaceFluxMetadata(contract.metadata, contract.context),
    /calibration does not cover/,
  );
});

test("surface-flux metadata rejects cross-profile sampling identities", () => {
  const contract = fixtureV2("standard");
  contract.context.identity.sampling_profile_id = OPTIMIZED_SAMPLING_PROFILE;
  assert.throws(
    () => validateSurfaceFluxMetadata(contract.metadata, contract.context),
    /calibration does not cover/,
  );
});

test("schema v2 maps four qualities to two local-patch families and separate palettes", () => {
  const expected = {
    direct: ["neutral-upper-hemisphere-standard-front-local-patch-v1", "standard", true],
    standard: ["neutral-upper-hemisphere-standard-front-local-patch-v1", "standard", false],
    quality: ["neutral-upper-hemisphere-quality-front-local-patch-v1", "quality", false],
    rigorous: ["neutral-upper-hemisphere-quality-front-local-patch-v1", "quality", true],
  };
  for (const [quality, [profileId, sourceQuality, proxy]] of Object.entries(expected)) {
    const contract = fixtureV2(quality);
    const selected = qualityFamilyFor(quality, 2);
    const validated = validateSurfaceFluxMetadata(contract.metadata, contract.context);
    assert.equal(selected.profile.profile_id, profileId);
    assert.equal(selected.profile.calibration_source_quality, sourceQuality);
    assert.equal(selected.proxy, proxy);
    assert.equal(validated.family.profile.profile_id, profileId);
    assert.equal(validated.family.proxy, proxy);
    assert.deepEqual(contract.metadata.palettes.front.anchors_u, FRONT_LOCAL_PATCH_ANCHORS);
    assert.deepEqual(contract.metadata.palettes.front.colors, FRONT_LOCAL_PATCH_PALETTE);
    assert.deepEqual(contract.metadata.palettes.back.anchors_z, BACK_ANCHORS);
    assert.deepEqual(contract.metadata.palettes.back.colors, BACK_PALETTE);
    assert.notDeepEqual(
      contract.metadata.palettes.front.anchors_u,
      contract.metadata.palettes.back.anchors_z,
    );
    assert.notDeepEqual(
      contract.metadata.palettes.front.colors,
      contract.metadata.palettes.back.colors,
    );
  }
});

test("schema v2 coefficients require asynchronous Float64 hash authentication", async () => {
  const contract = fixtureV2("standard");
  const merelyValidated = validateSurfaceFluxMetadata(contract.metadata, contract.context);
  assert.equal(merelyValidated.schemaVersion, 2);
  const surface = plantSurface(2);
  const raw = new Float32Array(2 * 192 * 4).fill(1);
  assert.throws(
    () => createSurfaceFluxColorController(
      surface, { patchCount: 384, rawValues: raw }, contract.metadata, merelyValidated,
    ),
    /unauthenticated/,
  );
  assert.equal(raw.every((value) => value === 0), true);
  raw.fill(1);

  const authenticated = await authenticateSurfaceFluxMetadata(
    contract.metadata, contract.context,
  );
  const controller = createSurfaceFluxColorController(
    surface, { patchCount: 384, rawValues: raw }, contract.metadata, authenticated,
  );
  assert.equal(controller.getState().disposed, false);
  controller.dispose();
  assert.equal(raw.every((value) => value === 0), true);

  const changed = fixtureV2("standard");
  changed.metadata.selected_calibration_profile.front_local_patch_metrics
    .incident_par.coefficients.reverse();
  assert.equal(
    validateSurfaceFluxMetadata(changed.metadata, changed.context).schemaVersion,
    2,
  );
  await assert.rejects(
    authenticateSurfaceFluxMetadata(changed.metadata, changed.context),
    /SHA-256 validation/,
  );
  surface.geometry.dispose();
  surface.material.dispose();
});

test("schema-v2 lighting-system names cannot change calibration-family selection", () => {
  for (const systemId of ["proposed", "conventional", "hps", "unknown"]) {
    const contract = fixtureV2("quality");
    contract.context.scene.run.system_id = systemId;
    const validated = validateSurfaceFluxMetadata(contract.metadata, contract.context);
    assert.equal(validated.family.profile.profile_id,
      "neutral-upper-hemisphere-quality-front-local-patch-v1");
  }
});

test("schema hashes counts topology order quality reference material and far-red fail closed", () => {
  const cases = [
    (value) => { value.metadata.schema_id = "wrong"; },
    (value) => { value.metadata.calibration_provenance.configuration_sha256 = "0".repeat(64); },
    (value) => { value.metadata.topology.counts.patches += 1; },
    (value) => { value.metadata.topology.topology_sha256 = "0".repeat(64); },
    (value) => { value.context.identity.receiver_side[0] = "back"; },
    (value) => { value.metadata.quality_family.selected_profile_id = "wrong"; },
    (value) => { value.metadata.reference.value_kind = "achieved"; },
    (value) => { value.metadata.metrics.far_red_excluded = false; },
    (value) => { value.metadata.scientific_artifact_boundary.material_coefficient_authorities[0].band_id = "red"; },
    (value) => { value.metadata.scientific_artifact_boundary.patch_float64_artifact.sha256 = "bad"; },
    (value) => { value.metadata.palette.front_anchors_z[1] = 0.2; },
    (value) => { value.metadata.legends.incident_par.front.anchors[1].q_umol_m2_s += 1; },
  ];
  for (const mutate of cases) {
    const value = fixture("standard");
    mutate(value);
    assert.throws(() => validateSurfaceFluxMetadata(value.metadata, value.context));
  }
});

test("invalid schema-v2 metadata or raw values return a neutral fallback", async () => {
  const originalFetch = globalThis.fetch;
  const originalLocation = globalThis.location;
  globalThis.location = { href: "https://viewer.invalid/index.html" };
  try {
    const invalidCases = [
      {
        label: "schema identity",
        mutateMetadata(metadata) { metadata.schema_id = "wrong"; },
        expected: /schema/i,
      },
      {
        label: "profile coefficient array authentication",
        mutateMetadata(metadata) {
          metadata.selected_calibration_profile.front_local_patch_metrics
            .absorbed_par.coefficients.reverse();
        },
        expected: /SHA-256 validation/i,
      },
      {
        label: "profile coefficient hash",
        mutateMetadata(metadata) {
          metadata.selected_calibration_profile.front_local_patch_metrics
            .incident_par.float64_little_endian_sha256 = "0".repeat(64);
        },
        expected: /coefficients are incompatible/i,
      },
      {
        label: "non-positive profile coefficient",
        mutateMetadata(metadata) {
          metadata.selected_calibration_profile.front_local_patch_metrics
            .incident_par.coefficients[0] = 0;
        },
        expected: /coefficients are incompatible/i,
      },
      {
        label: "quality-family mapping",
        mutateMetadata(metadata) {
          metadata.quality_family_mapping.direct.selected_profile_id = "wrong";
        },
        expected: /mapping/i,
      },
      {
        label: "topology mapping",
        mutateMetadata(metadata) {
          metadata.topology.global_patch_equation = "global_patch_index = local_patch_index";
        },
        expected: /topology|mapping/i,
      },
      {
        label: "topology ordering",
        mutateMetadata(metadata) { metadata.topology.ordering = "global patch order"; },
        expected: /topology/i,
      },
      {
        label: "front palette",
        mutateMetadata(metadata) { metadata.palettes.front.anchors_u[1] = 0.60; },
        expected: /palette/i,
      },
      {
        label: "back palette",
        mutateMetadata(metadata) { metadata.palettes.back.anchors_z[1] = 0.03; },
        expected: /palette/i,
      },
      {
        label: "front display equation",
        mutateMetadata(metadata) {
          metadata.display_equation.front.front_beta_used_for_coloring = true;
        },
        expected: /equation/i,
      },
      {
        label: "calibration limitations",
        mutateMetadata(metadata) { metadata.calibration_limitations.pop(); },
        expected: /limitations/i,
      },
      {
        label: "front legend",
        mutateMetadata(metadata) {
          metadata.legends.incident_par.front.anchors[1]
            .q_minimum_umol_m2_s += 1;
        },
        expected: /threshold/i,
      },
      {
        label: "raw channel semantics",
        mutateMetadata(metadata) {
          metadata.display_artifact.channel_semantics = "front u and back z";
        },
        expected: /raw-q/i,
      },
      {
        label: "raw patch values",
        mutateValues(bytes) { new DataView(bytes).setFloat32(0, Number.NaN, true); },
        expected: /non-finite/i,
      },
    ];
    for (const invalid of invalidCases) {
      const contract = fixtureV2("standard");
      const valueBytes = new ArrayBuffer(
        contract.metadata.topology.counts.patches * 16,
      );
      invalid.mutateValues?.(valueBytes);
      contract.metadata.display_artifact.sha256 = await sha256Hex(valueBytes);
      invalid.mutateMetadata?.(contract.metadata);
      const metadataBytes = new TextEncoder().encode(JSON.stringify(contract.metadata));
      contract.context.scene.surface_flux = {
        availability: "available",
        metadata: {
          filename: "surface-flux/metadata.v2.json",
          byte_length: metadataBytes.byteLength,
          sha256: await sha256Hex(metadataBytes),
        },
      };
      const abort = new AbortController();
      contract.context.signal = abort.signal;
      globalThis.fetch = async (url, options) => {
        assert.equal(options.signal, abort.signal);
        if (url.pathname.endsWith("/surface-flux/metadata.v2.json")) {
          return {
            ok: true,
            status: 200,
            arrayBuffer: async () => metadataBytes.buffer.slice(
              metadataBytes.byteOffset,
              metadataBytes.byteOffset + metadataBytes.byteLength,
            ),
          };
        }
        assert.equal(
          url.pathname.endsWith("/surface-flux/patch-values.v1.f32le.bin"),
          true,
        );
        return { ok: true, status: 200, arrayBuffer: async () => valueBytes };
      };
      const result = await loadValidatedSurfaceFluxArtifacts(
        "./scene.v1.json", contract.context,
      );
      assert.equal(result.available, false, invalid.label);
      assert.equal(result.payload, undefined, invalid.label);
      assert.equal(result.validated, undefined, invalid.label);
      assert.match(result.diagnostic, invalid.expected, invalid.label);
    }
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.location = originalLocation;
  }
});

test("aborted schema-v2 artifact requests return a neutral fallback", async () => {
  const contract = fixtureV2("standard");
  contract.context.scene.surface_flux = {
    availability: "available",
    metadata: {
      filename: "surface-flux/metadata.v2.json",
      byte_length: 1,
      sha256: HASH,
    },
  };
  const abort = new AbortController();
  abort.abort();
  contract.context.signal = abort.signal;
  const originalFetch = globalThis.fetch;
  const originalLocation = globalThis.location;
  globalThis.location = { href: "https://viewer.invalid/index.html" };
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(options.signal.aborted, true);
      throw new Error(`aborted ${url.pathname}`);
    };
    const aborted = await loadValidatedSurfaceFluxArtifacts(
      "./scene.v1.json", contract.context,
    );
    assert.equal(aborted.available, false);
    assert.match(aborted.diagnostic, /aborted/);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.location = originalLocation;
  }
});

test("schema-v2 Float32 parsing preserves raw q and rejects invalid values", () => {
  const value = fixtureV2("quality");
  const count = value.metadata.topology.counts.patches * 4;
  const source = new Float32Array(count);
  for (let index = 0; index < count; index += 1) source[index] = index / 10;
  const bytes = source.buffer.slice(0);
  const parsed = parseSurfaceFluxValues(bytes, value.metadata);
  assert.deepEqual([...parsed.values], [...source]);
  assert.deepEqual([...new Float32Array(bytes)], [...source]);
  const parsedValues = parsed.rawValues;
  parsed.release();
  assert.equal(parsed.rawValues, null);
  assert.equal(parsedValues.every((item) => item === 0), true);
  assert.throws(() => parseSurfaceFluxValues(bytes.slice(4), value.metadata), /length|stride/);
  new DataView(bytes).setFloat32(0, -1, true);
  assert.throws(() => parseSurfaceFluxValues(bytes, value.metadata), /negative/);
  new DataView(bytes).setFloat32(0, Number.NaN, true);
  assert.throws(() => parseSurfaceFluxValues(bytes, value.metadata), /non-finite/);
  const implementation = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/surface-flux.js", import.meta.url),
    "utf8",
  );
  assert.match(implementation, /values\.fill\(0\);[\s\S]*non-finite or negative value/);
  assert.match(implementation, /catch \(error\) \{\s*display\.fill\(0\);/);
});

test("canonical GPU lookup and front/back modes are exact", () => {
  assert.equal(globalPatchIndex(0, 0, 64), 0);
  assert.equal(globalPatchIndex(0, 191, 64), 191);
  assert.equal(globalPatchIndex(63, 0, 64), 12096);
  assert.equal(globalPatchIndex(63, 191, 64), 12287);
  assert.throws(() => globalPatchIndex(64, 0, 64));
  assert.throws(() => globalPatchIndex(0, 192, 64));
  assert.equal(resolveSurfaceSide("both", true), "front");
  assert.equal(resolveSurfaceSide("both", false), "back");
  assert.equal(resolveSurfaceSide("front", true), "front");
  assert.equal(resolveSurfaceSide("front", false), null);
  assert.equal(resolveSurfaceSide("back", true), null);
  assert.equal(resolveSurfaceSide("back", false), "back");
});

test("schema-v2 raw q becomes front u by local k and unchanged back z", async () => {
  const contract = fixtureV2("standard", 64);
  const authenticated = await authenticateSurfaceFluxMetadata(
    contract.metadata, contract.context,
  );
  const surface = plantSurface(64);
  const raw = new Float32Array(64 * 192 * 4);
  const reference = contract.metadata.reference.value;
  const selected = contract.metadata.selected_calibration_profile;
  const incidentGamma = selected.front_local_patch_metrics.incident_par.coefficients;
  const absorbedGamma = selected.front_local_patch_metrics.absorbed_par.coefficients;
  const betas = selected.scalar_beta_provenance.coefficients;
  const cases = [
    [0, 0, 0.59, 0.25, 1.14, 2.75],
    [0, 191, 0.81, 0.50, 1.52, 1.00],
    [63, 0, 1.00, 1.25, 0.89, 0.14],
    [63, 191, 2.84, 5.33, 0.40, 0.05],
  ];
  for (const [plant, localPatch, frontIncidentU, backIncidentZ,
    frontAbsorbedU, backAbsorbedZ] of cases) {
    const offset = globalPatchIndex(plant, localPatch, 64) * 4;
    raw[offset] = incidentGamma[localPatch] * reference * frontIncidentU;
    raw[offset + 1] = betas.back_incident * reference * backIncidentZ;
    raw[offset + 2] = absorbedGamma[localPatch] * reference * frontAbsorbedU;
    raw[offset + 3] = betas.back_absorbed * reference * backAbsorbedZ;
  }
  const rawSnapshot = new Float32Array(raw);
  const controller = createSurfaceFluxColorController(
    surface, { patchCount: 64 * 192, rawValues: raw }, contract.metadata, authenticated,
  );
  const display = controller.texture.image.data;
  assert.notEqual(display, raw);
  assert.deepEqual(raw, rawSnapshot);
  for (const [plant, localPatch, frontIncidentU, backIncidentZ,
    frontAbsorbedU, backAbsorbedZ] of cases) {
    const globalPatch = globalPatchIndex(plant, localPatch, 64);
    const offset = globalPatch * 4;
    assertClose(display[offset], frontIncidentU);
    assertClose(display[offset + 1], backIncidentZ);
    assertClose(display[offset + 2], frontAbsorbedU);
    assertClose(display[offset + 3], backAbsorbedZ);
    assert.deepEqual(controller.getRawPatchValues(plant, localPatch), {
      front_incident_par: raw[offset],
      back_incident_par: raw[offset + 1],
      front_absorbed_par: raw[offset + 2],
      back_absorbed_par: raw[offset + 3],
    });
  }
  controller.dispose();
  assert.equal(raw.every((value) => value === 0), true);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("patch attribute uses a float GPU binding and centered exact texel lookup", () => {
  const attributes = {
    POSITION: new Float32Array(9), NORMAL: new Float32Array(9),
    _LEAF_INDEX: new Uint32Array(3), _FACE_INDEX: new Uint32Array(3),
    _PATCH_INDEX: new Uint32Array([0, 191, 1]),
  };
  const surface = createPlantSurface({ attributes }, 2);
  const patchAttribute = surface.geometry.getAttribute("_patch_index");
  assert.equal(patchAttribute.array instanceof Float32Array, true);
  assert.notEqual(patchAttribute.array, attributes._PATCH_INDEX);
  assert.equal(patchAttribute.normalized, false);
  assert.equal(patchAttribute.gpuType, THREE.FloatType);

  const implementation = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/surface-flux.js", import.meta.url),
    "utf8",
  );
  assert.match(implementation, /attribute float _patch_index;/);
  assert.doesNotMatch(implementation, /attribute\s+(?:int|uint)\s+_patch_index;/);
  assert.match(implementation,
    /surfaceFluxLocalPatchIndex = floor\(_patch_index \+ 0\.5\);/);
  assert.match(implementation,
    /vec2\(surfaceFluxLocalPatchIndex, float\(gl_InstanceID\)\) \+ vec2\(0\.5\)/);
  assert.match(implementation, /\/ surfaceFluxTextureSize;/);

  for (const [vertex, expected, plantIndex] of [[0, 0, 0], [1, 191, 1]]) {
    const converted = Math.floor(patchAttribute.getX(vertex) + 0.5);
    assert.equal(converted, expected);
    assert.deepEqual(
      [(converted + 0.5) / 192, (plantIndex + 0.5) / 2],
      [(expected + 0.5) / 192, (plantIndex + 0.5) / 2],
    );
  }
  assert.equal(globalPatchIndex(0, 0, 2), 192 * 0 + 0);
  assert.equal(globalPatchIndex(1, 191, 2), 192 * 1 + 191);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("schema v2 colors the existing InstancedMesh with one side-specific texture", async () => {
  const implementation = readFileSync(
    new URL("../src/fspm_optics/resources/viewer/surface-flux.js", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(implementation, /new THREE\.(Mesh|InstancedMesh|ShaderMaterial)/);
  assert.equal((implementation.match(/new THREE\.DataTexture\(/g) ?? []).length, 1);
  const contract = fixtureV2("standard");
  const authenticated = await authenticateSurfaceFluxMetadata(
    contract.metadata, contract.context,
  );
  const surface = plantSurface(2);
  const originalCompile = surface.material.onBeforeCompile;
  const originalCacheKey = surface.material.customProgramCacheKey;
  const values = new Float32Array(2 * 192 * 4).fill(100);
  let firstReleased = false;
  const first = createSurfaceFluxColorController(
    surface, {
      patchCount: 384,
      rawValues: values,
      release() { firstReleased = true; },
    }, contract.metadata, authenticated,
  );
  assert.equal(surface.isInstancedMesh, true);
  assert.equal(surface.children.length, 0);
  assert.equal(surface.material.isMeshStandardMaterial, true);
  assert.equal(first.texture.image.width, 192);
  assert.equal(first.texture.image.height, 2);
  assert.notEqual(first.texture.image.data, values);
  const firstDisplayValues = first.texture.image.data;
  first.setMetric("absorbed_par");
  first.setSides("back");
  assert.deepEqual(first.getState(), {
    disposed: false, enabled: true, metric: "absorbed_par", sides: "back",
  });
  const shader = {
    uniforms: {},
    vertexShader: "#include <common>\n#include <begin_vertex>",
    fragmentShader: "#include <common>\n#include <color_fragment>",
  };
  surface.material.onBeforeCompile(shader, {});
  assert.match(shader.vertexShader, /gl_InstanceID/);
  assert.match(shader.vertexShader, /_patch_index/);
  assert.match(shader.fragmentShader, /gl_FrontFacing/);
  assert.match(shader.fragmentShader, /diffuseColor\.rgb/);
  assert.match(shader.fragmentShader,
    /frontSide \? vSurfaceFluxQ\.r : vSurfaceFluxQ\.g/);
  assert.match(shader.fragmentShader,
    /frontSide \? vSurfaceFluxQ\.b : vSurfaceFluxQ\.a/);
  assert.match(shader.fragmentShader,
    /frontSide \? surfaceFluxFrontAnchors\[0\] : surfaceFluxBackAnchors\[0\]/);
  assert.match(shader.fragmentShader,
    /frontSide \? surfaceFluxFrontPalette\[0\] : surfaceFluxBackPalette\[0\]/);
  assert.equal(shader.uniforms.surfaceFluxValuesAreRelative.value, 1);
  assert.deepEqual(
    shader.uniforms.surfaceFluxFrontAnchors.value,
    FRONT_LOCAL_PATCH_ANCHORS,
  );
  assert.deepEqual(shader.uniforms.surfaceFluxBackAnchors.value, BACK_ANCHORS);
  assert.notDeepEqual(
    shader.uniforms.surfaceFluxFrontAnchors.value,
    shader.uniforms.surfaceFluxBackAnchors.value,
  );

  let firstDisposed = false;
  first.texture.addEventListener("dispose", () => { firstDisposed = true; });
  let secondReleased = false;
  const secondValues = new Float32Array(values);
  const second = createSurfaceFluxColorController(
    surface, {
      patchCount: 384,
      rawValues: secondValues,
      release() { secondReleased = true; },
    }, contract.metadata, authenticated,
  );
  assert.equal(firstDisposed, true);
  assert.equal(firstReleased, true);
  assert.equal(values.every((value) => value === 0), true);
  assert.equal(firstDisplayValues.every((value) => value === 0), true);
  assert.equal(first.getState().disposed, true);
  assert.equal(first.getRawPatchValues(0, 0), null);
  assert.equal(first.texture.image.data, null);
  assert.equal(surface.material.userData.surfaceFluxShader, undefined);
  const secondShader = {
    uniforms: {},
    vertexShader: "#include <common>\n#include <begin_vertex>",
    fragmentShader: "#include <common>\n#include <color_fragment>",
  };
  surface.material.onBeforeCompile(secondShader, {});
  assert.equal(surface.material.userData.surfaceFluxShader, secondShader);
  const secondDisplayValues = second.texture.image.data;
  let secondDisposed = false;
  second.texture.addEventListener("dispose", () => { secondDisposed = true; });
  clearSurfaceFluxColoring(surface);
  assert.equal(secondDisposed, true);
  assert.equal(secondReleased, true);
  assert.equal(secondValues.every((value) => value === 0), true);
  assert.equal(secondDisplayValues.every((value) => value === 0), true);
  assert.equal(second.texture.image.data, null);
  assert.equal(surface.userData.surfaceFluxController, undefined);
  assert.equal(surface.material.userData.surfaceFluxShader, undefined);
  assert.equal(surface.material.onBeforeCompile, originalCompile);
  assert.equal(surface.material.customProgramCacheKey, originalCacheKey);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("D4 surface flux preserves palette color and restores the leaf shader hook", async () => {
  const contract = fixtureV2("standard");
  const authenticated = await authenticateSurfaceFluxMetadata(
    contract.metadata, contract.context,
  );
  const attributes = {
    POSITION: new Float32Array(9), NORMAL: new Float32Array(9),
    TEXCOORD_0: new Float32Array([0, 0.5, 0.5, 0, 1, 0.5]),
    _LEAF_INDEX: new Uint32Array(3), _FACE_INDEX: new Uint32Array(3),
    _PATCH_INDEX: new Uint32Array(3),
  };
  const surface = applyInstanceTranslations(
    createPlantSurface({ attributes }, 2),
    { count: 2, values: new Float32Array(6) },
  );
  const leafCompile = surface.material.onBeforeCompile;
  const leafCacheKey = surface.material.customProgramCacheKey;
  const values = new Float32Array(2 * 192 * 4).fill(100);
  const controller = createSurfaceFluxColorController(
    surface, { patchCount: 384, rawValues: values },
    contract.metadata, authenticated,
  );
  const shader = {
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
  surface.material.onBeforeCompile(shader, {});
  assert.match(
    shader.fragmentShader,
    /diffuseColor\.rgb = mapSurfaceFluxColor\(relativeValue, frontSide\)\s*\* leafDetail\.fluxLuminance;/,
  );
  assert.match(shader.fragmentShader, /leafFluxColoredSide = true;/);
  assert.doesNotMatch(
    shader.fragmentShader,
    /mapSurfaceFluxColor\(relativeValue, frontSide\)[^;]*leafNeutralColor/,
  );
  assert.ok(
    shader.fragmentShader.indexOf("leafNeutralColor")
      < shader.fragmentShader.indexOf("mapSurfaceFluxColor(relativeValue, frontSide)"),
  );
  assert.ok(
    shader.fragmentShader.indexOf("leafFluxColoredSide = true")
      < shader.fragmentShader.indexOf("float leafRoughnessDetail"),
  );
  assert.equal(shader.uniforms.leafFluxVeinContrastMaximum.value, 0.01);
  assert.equal(shader.uniforms.leafScientificOverlayVisible.value, 0);
  controller.dispose();
  assert.equal(surface.material.onBeforeCompile, leafCompile);
  assert.equal(surface.material.customProgramCacheKey, leafCacheKey);
  surface.userData.leafMaterialController.dispose();
  surface.geometry.dispose();
  surface.material.dispose();
});

test("explicit schema-v1 dispatch retains scalar beta shader normalization", () => {
  const contract = fixture("standard");
  const surface = plantSurface(2);
  const values = new Float32Array(2 * 192 * 4).fill(100);
  const controller = createSurfaceFluxColorController(
    surface, { patchCount: 384, values }, contract.metadata,
  );
  assert.equal(controller.texture.image.data, values);
  const shader = {
    uniforms: {},
    vertexShader: "#include <common>\n#include <begin_vertex>",
    fragmentShader: "#include <common>\n#include <color_fragment>",
  };
  surface.material.onBeforeCompile(shader, {});
  assert.equal(shader.uniforms.surfaceFluxValuesAreRelative.value, 0);
  assert.deepEqual(shader.uniforms.surfaceFluxFrontAnchors.value, FRONT_ANCHORS);
  assert.deepEqual(shader.uniforms.surfaceFluxBackAnchors.value, BACK_ANCHORS);
  assert.deepEqual(
    shader.uniforms.surfaceFluxBetas.value.toArray(),
    [
      contract.metadata.selected_calibration_profile.coefficients.front_incident,
      contract.metadata.selected_calibration_profile.coefficients.back_incident,
      contract.metadata.selected_calibration_profile.coefficients.front_absorbed,
      contract.metadata.selected_calibration_profile.coefficients.back_absorbed,
    ],
  );
  assert.match(shader.fragmentShader, /relativeValue = relativeValue \/ \(beta \* surfaceFluxReference\)/);
  assert.match(surface.material.customProgramCacheKey(), /schema-1$/);
  controller.dispose();
  assert.equal(values.every((value) => value === 0), true);
  surface.geometry.dispose();
  surface.material.dispose();
});

test("GPU allocation rejects wrong counts and unbounded plant dimensions", () => {
  const contract = fixture("standard");
  const surface = plantSurface(2);
  assert.throws(() => createSurfaceFluxColorController(
    surface, { patchCount: 1, values: new Float32Array(4) }, contract.metadata,
  ), /incompatible|unbounded/);
  contract.metadata.topology.counts.plants = MAX_DISPLAY_PLANT_COUNT + 1;
  assert.throws(() => createSurfaceFluxColorController(
    surface, { patchCount: 384, values: new Float32Array(1536) }, contract.metadata,
  ), /incompatible|unbounded/);
  surface.geometry.dispose();
  surface.material.dispose();
});

function plantSurface(count) {
  const attributes = {
    POSITION: new Float32Array(9), NORMAL: new Float32Array(9),
    _LEAF_INDEX: new Uint32Array(3), _FACE_INDEX: new Uint32Array(3),
    _PATCH_INDEX: new Uint32Array(3),
  };
  const surface = createPlantSurface({ attributes }, count);
  return applyInstanceTranslations(surface, {
    count, values: new Float32Array(count * 3),
  });
}

function fixtureV2(quality, plants = 2) {
  const policy = qualityFamilyFor(quality, 2);
  const profile = policy.profile;
  const scalarProfile = profile.scalarProfile;
  const resourceProfile = CALIBRATION_RESOURCE.profiles[
    profile.calibration_source_quality
  ];
  const reference = {
    value: 500,
    units: "umol/m^2/s",
    value_kind: "requested",
    operating_policy: "target-controlled",
    source_provenance: {
      stage_id: "baseline_ppfd",
      artifact: "request",
      field: "target_ppfd_umol_m2_s",
    },
  };
  const counts = {
    plants,
    leaves: plants * 12,
    faces: plants * 1920,
    patches: plants * 192,
    receivers: plants * 384,
  };
  const frontMetrics = Object.fromEntries(
    ["incident_par", "absorbed_par"].map((metric) => {
      const source = resourceProfile.metrics[metric];
      return [metric, {
        coefficient_count: 192,
        coefficient_order: "canonical local_patch_index 0..191",
        coefficients: [...source.coefficients],
        derivation: source.derivation,
        float64_little_endian_sha256: source.float64_little_endian_sha256,
      }];
    }),
  );
  const scalar = {
    back_used_for_coloring: true,
    coefficient_equation: "z = q / (beta * R)",
    coefficients: { ...scalarProfile.coefficients },
    fit_basis: scalarProfile.fit_basis,
    front_used_for_coloring: false,
  };
  if (scalarProfile.achieved_reference_ppfd_umol_m2_s !== undefined) {
    scalar.achieved_reference_ppfd_umol_m2_s =
      scalarProfile.achieved_reference_ppfd_umol_m2_s;
  }
  const selectedProfile = {
    calibration_source_quality: profile.calibration_source_quality,
    family_id: profile.family_id,
    front_local_patch_metrics: frontMetrics,
    profile_id: profile.profile_id,
    scalar_beta_provenance: scalar,
  };
  const qualityFamilyMapping = Object.fromEntries(
    ["direct", "standard", "quality", "rigorous"].map((mappedQuality) => {
      const mapped = qualityFamilyFor(mappedQuality, 2);
      return [mappedQuality, {
        calibration_source_quality: mapped.profile.calibration_source_quality,
        family_id: mapped.profile.family_id,
        proxy: mapped.proxy,
        proxy_rationale: mapped.proxyRationale,
        selected_profile_id: mapped.profile.profile_id,
      }];
    }),
  );
  const frontPalette = {
    anchors_u: [...FRONT_LOCAL_PATCH_ANCHORS],
    colors: FRONT_LOCAL_PATCH_PALETTE.map((color) => ({ ...color })),
    continuous_interpolation: true,
    green_interval_u: [0.89, 1.14],
    palette_id: "surface-flux-front-neutral-local-patch-blue-to-red-8-v1",
    per_run_extrema_normalization: false,
    shared_across_metrics_systems_and_quality_families: true,
    variable: "u",
  };
  const backPalette = {
    anchors_z: [...BACK_ANCHORS],
    colors: BACK_PALETTE.map((color) => ({ ...color })),
    continuous_interpolation: true,
    palette_id: "surface-flux-blue-to-red-8-v1",
    per_run_extrema_normalization: false,
    shared_across_metrics_systems_and_quality_families: true,
    variable: "z",
  };
  const thresholdRule = "q_anchor[k] = u_anchor * gamma[k] * R";
  const legends = Object.fromEntries(
    ["incident_par", "absorbed_par"].map((metric) => {
      const suffix = metric.replace("_par", "");
      const gamma = frontMetrics[metric];
      const gammaMinimum = Math.min(...gamma.coefficients);
      const gammaMaximum = Math.max(...gamma.coefficients);
      const frontBeta = scalar.coefficients[`front_${suffix}`];
      const backBeta = scalar.coefficients[`back_${suffix}`];
      return [metric, {
        front: {
          anchors: FRONT_LOCAL_PATCH_ANCHORS.map((u, index) => ({
            color_name: FRONT_LOCAL_PATCH_PALETTE[index].name,
            q_maximum_umol_m2_s: u * gammaMaximum * reference.value,
            q_minimum_umol_m2_s: u * gammaMinimum * reference.value,
            q_threshold_rule: thresholdRule,
            srgb_hex: FRONT_LOCAL_PATCH_PALETTE[index].srgb_hex,
            u,
          })),
          beta_provenance: { beta: frontBeta, used_for_coloring: false },
          clipping: clipping("u", FRONT_LOCAL_PATCH_ANCHORS),
          coefficient_hash: gamma.float64_little_endian_sha256,
          gamma_profile_id: profile.profile_id,
          metric,
          physical_threshold_rule: thresholdRule,
          raw_maximum_q: 1000,
          raw_minimum_q: 0,
          reference: structuredClone(reference),
          side: "front",
          u_equals_one_meaning:
            "expected-equivalent exposure for the same local patch; not a leaf target and not raw-q uniformity",
          units: "umol/m^2/s",
          variable: "u",
        },
        back: {
          anchors: BACK_ANCHORS.map((z, index) => ({
            color_name: BACK_PALETTE[index].name,
            q_umol_m2_s: z * backBeta * reference.value,
            srgb_hex: BACK_PALETTE[index].srgb_hex,
            z,
          })),
          beta: backBeta,
          clipping: clipping("z", BACK_ANCHORS),
          metric,
          raw_maximum_q: 1000,
          raw_minimum_q: 0,
          reference: structuredClone(reference),
          side: "back",
          units: "umol/m^2/s",
          z_equals_one_meaning:
            "expected-equivalent neutral-reference exposure; not a leaf target",
        },
      }];
    }),
  );
  const metadata = {
    schema_id: "fspm-optics.surface-flux-display",
    schema_version: 2,
    availability: "available",
    run_id: "a".repeat(32),
    profile_id: PROFILE_ID,
    quality_family: {
      evaluated_quality: quality,
      selected_profile_id: profile.profile_id,
      calibration_source_quality: profile.calibration_source_quality,
      family_id: profile.family_id,
      proxy: policy.proxy,
      proxy_rationale: policy.proxyRationale,
    },
    quality_family_mapping: qualityFamilyMapping,
    selected_calibration_profile: selectedProfile,
    calibration_provenance: {
      report_schema_id: "fspm-optics.surface-flux-calibration-report",
      report_schema_version: 1,
      original_d1_experiment_status: "complete_fail",
      original_d1_acceptance_pass: false,
      original_d1_outcome_must_not_be_rewritten: true,
      original_cross_quality_convergence_rewritten: false,
      repository_revision: "3a35cc3a8d143e624a468049344ed15676d791e8",
      configuration_sha256: "d388d6293a94102232f5211dd8bb0b942b401ea30794a63c9f36d8920ae4d9b0",
      source_model_id: "neutral-uniform-upper-hemisphere-v1",
      source_definition_sha256: "cfe8b701186ef8c2cd49ae8d8e9783968353cbc9383e47e3674b8909bb3e6530",
      calibration_scene_sha256: "a3bf55f2e2755ee74a28c6285d8b2408ead18ca3c416487988b238debe784d32",
      topology_sha256: "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09",
      receivers_sha256: "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc",
      material_plan_sha256: "c5d829ecdbde2b06602468e187fd75c6314f55e671b2d5529289d40adb4ccff6",
      source_scope: "open-boundary uniform upper-hemisphere neutral field",
      front_local_patch_calibration: {
        schema_id: "fspm-optics.surface-flux-front-local-patch-calibration-input",
        schema_version: 1,
        resource_sha256: "6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3",
        combined_float64_little_endian_sha256:
          "b1e3cec31fc4b4e81ce03fd88291edaafd8bb6a717a4d228e57c60d61bd21a07",
      },
    },
    reference,
    display_equation: {
      front: {
        formula: "u[p,k,m] = q[p,k,m] / (gamma[quality_family,m,k] * R)",
        front_beta_used_for_coloring: false,
        gamma_index: "canonical local_patch_index k in [0, 191], reused across plants",
        q_authority: "Phase 27G-C raw Float64 PAR surface-light density",
        u_equals_one_meaning:
          "expected-equivalent exposure for the same local patch; not a leaf target and not raw-q uniformity",
        variable: "u",
      },
      back: {
        back_beta_used_for_coloring: true,
        formula: "z = q / (beta * R)",
        q_authority: "Phase 27G-C raw Float64 PAR surface-light density",
        variable: "z",
        z_equals_one_meaning:
          "expected-equivalent neutral-reference exposure; not a leaf target",
      },
      raw_q_modified: false,
      transport_correction: false,
    },
    calibration_limitations: [
      "Open-boundary uniform-upper-hemisphere calibration.",
      "Frozen juvenile Rex topology/material scope.",
      "Direct and Rigorous remain declared quality-family proxies.",
      "Local-patch normalization removes fixed morphology response but intentionally preserves plant-position, occlusion, and directional-lighting differences.",
      "Back receiver calibration and palette are intentionally deferred.",
    ],
    metrics: {
      available: ["incident_par", "absorbed_par"],
      incident_par: "blue + green + orange + red incident density",
      absorbed_par: "blue + green + orange + red absorbed density",
      far_red_excluded: true,
    },
    palettes: { front: frontPalette, back: backPalette },
    legends,
    scientific_artifact_boundary: scientificBoundary(counts),
    topology: {
      canonical_profile_id: PROFILE_ID,
      topology_sha256: "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09",
      receivers_sha256: "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc",
      counts,
      local_patches_per_plant: 192,
      global_patch_equation: "global_patch_index = 192 * plant_index + local_patch_index",
      instance_mapping: "instance_id equals canonical plant_index",
      front_back_selection: "fragment gl_FrontFacing selects front or back",
      layout_plan_hash: HASH,
      ordering: "Y-major/X-minor plants; plant-major canonical patches",
      front_receiver_equation: "384 * plant_index + 2 * local_patch_index",
      coefficient_is_average_over_all_64_plants: true,
      coefficient_position_dependence: false,
    },
    display_artifact: {
      filename: "surface-flux/patch-values.v1.f32le.bin",
      byte_length: counts.patches * 16,
      sha256: HASH,
      component_type: "float32",
      byte_order: "little-endian",
      stride_bytes: 16,
      row_count: counts.patches,
      record_layout:
        "front_incident_par, back_incident_par, front_absorbed_par, back_absorbed_par",
      texture_layout: {
        format: "RGBA32F",
        width: 192,
        height: plants,
        texel_count: counts.patches,
        bounded_maximum_plant_count: 625,
      },
      channel_semantics: "raw q; neither front u nor back z",
      channel_units: "umol/m^2/s",
      raw_q_float32_derivative: true,
      raw_q_modified: false,
      normalization_applied_to_artifact: false,
    },
    failure_policy: {
      invalid_or_unavailable: "neutral plant material",
      clear_stale_gpu_resources: true,
      partial_scientific_coloring_allowed: false,
    },
  };
  const context = contextFor(plants);
  return { context, metadata };
}

function clipping(variable, anchors) {
  return {
    [`lower_bound_${variable}`]: anchors[0],
    [`upper_bound_${variable}`]: anchors.at(-1),
    below_count: 0,
    above_count: 0,
    below_physical_area_m2: 0,
    above_physical_area_m2: 0,
    below_physical_area_fraction: 0,
    above_physical_area_fraction: 0,
    total_physical_one_sided_area_m2: 1,
    raw_values_modified_by_clipping: false,
  };
}

function scientificBoundary(counts) {
  return {
    aggregation_schema_id: "fspm-optics.fspm-surface-light-aggregation",
    aggregation_schema_version: 1,
    aggregation_metadata_sha256: HASH,
    patch_float64_artifact: {
      role: "fspm_patch_surface_light",
      path: "fspm-aggregation/patch-surface-light.v1.f64le.bin",
      media_type: "application/octet-stream",
      byte_length: counts.patches * 232,
      sha256: HASH,
      row_count: counts.patches,
      stride_bytes: 232,
    },
    room_summary_artifact: {
      role: "fspm_room_surface_light_summary",
      path: "fspm-aggregation/room-summary.v1.json",
      media_type: "application/json",
      byte_length: 100,
      sha256: HASH,
    },
    transport_schema_id: "fspm-optics.juvenile-multi-plant-five-band-receivers",
    transport_schema_version: 2,
    transport_metadata_sha256: HASH,
    compact_receiver_index_sha256: HASH,
    material_coefficient_authorities: ["blue", "green", "orange", "red", "far_red"].map(
      (band_id) => ({
        band_id,
        material_sha256: HASH,
        material_provenance_sha256: HASH,
        raw_receiver_sha256: HASH,
      }),
    ),
    raw_float64_values_modified: false,
    far_red_read_into_display_values: false,
  };
}

function contextFor(plants) {
  return {
    scene: {
      schema_id: "fspm-optics.juvenile-rex-run-scene",
      schema_version: 2,
      viewer_resource_version: VIEWER_RESOURCE_VERSION,
      run: { run_id: "a".repeat(32), system_id: "conventional" },
      profile: {
        profile_id: PROFILE_ID,
        sampling_profile_id: LEGACY_SAMPLING_PROFILE,
      },
      natural_fit: { plant_count: plants, plan_hash: HASH },
      surface_flux: {
        availability: "available",
        metadata: {
          filename: "surface-flux/metadata.v2.json",
          byte_length: 1,
          sha256: HASH,
        },
      },
    },
    profile: {
      schema_id: "fspm-optics.juvenile-rex-viewer-profile",
      schema_version: 3,
      profile_id: PROFILE_ID,
      sampling_profile_id: LEGACY_SAMPLING_PROFILE,
      counts: { patches: 192 },
    },
    identity: {
      schema_id: "fspm-optics.juvenile-rex-identity-map",
      schema_version: 2,
      profile_id: PROFILE_ID,
      sampling_profile_id: LEGACY_SAMPLING_PROFILE,
      patch_ids: Array.from({ length: 192 }, (_, index) => `patch-${index}`),
      face_to_patch: Array.from({ length: 1920 }, (_, index) => Math.floor(index / 10)),
      patch_to_leaf: Array.from({ length: 192 }, (_, index) => Math.floor(index / 16)),
      receiver_to_patch: Array.from({ length: 384 }, (_, index) => Math.floor(index / 2)),
      receiver_side: Array.from(
        { length: 384 }, (_, index) => index % 2 ? "back" : "front",
      ),
    },
  };
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function assertClose(actual, expected, tolerance = 2e-5) {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${actual} differs from ${expected} by more than ${tolerance}`,
  );
}

function fixture(quality) {
  const plants = 2;
  const policy = qualityFamilyFor(quality);
  const profile = policy.profile;
  const reference = {
    value: 500, units: "umol/m^2/s", value_kind: "requested",
    operating_policy: "target-controlled",
    source_provenance: { stage_id: "baseline_ppfd", artifact: "request", field: "target_ppfd_umol_m2_s" },
  };
  const counts = { plants, leaves: 24, faces: 3840, patches: 384, receivers: 768 };
  const profilePayload = {
    profile_id: profile.profile_id, family_id: profile.family_id,
    calibration_source_quality: profile.calibration_source_quality,
    coefficients: { ...profile.coefficients }, coefficient_equation: "z = q / (beta * R)",
    fit_basis: profile.fit_basis,
  };
  if (profile.achieved_reference_ppfd_umol_m2_s !== undefined) {
    profilePayload.achieved_reference_ppfd_umol_m2_s = profile.achieved_reference_ppfd_umol_m2_s;
  }
  const qualityFamily = {
    evaluated_quality: quality, selected_profile_id: profile.profile_id,
    calibration_source_quality: profile.calibration_source_quality,
    family_id: profile.family_id, proxy: policy.proxy,
    proxy_rationale: policy.proxyRationale,
  };
  const legends = Object.fromEntries(["incident_par", "absorbed_par"].map((metric) => [
    metric,
    Object.fromEntries(["front", "back"].map((side) => {
      const beta = profile.coefficients[`${side}_${metric.replace("_par", "")}`];
      const anchors = side === "front" ? FRONT_ANCHORS : BACK_ANCHORS;
      return [side, {
        metric, side, units: "umol/m^2/s", beta, reference: { ...reference },
        raw_minimum_q: 0, raw_maximum_q: 1000,
        clipping: {
          lower_bound_z: anchors[0], upper_bound_z: anchors.at(-1),
          below_count: 0, above_count: 1,
          below_physical_area_m2: 0, above_physical_area_m2: 0.01,
          below_physical_area_fraction: 0, above_physical_area_fraction: 0.01,
          total_physical_one_sided_area_m2: 1, raw_values_modified_by_clipping: false,
        },
        anchors: anchors.map((z, index) => ({
          z, q_umol_m2_s: z * beta * reference.value,
          ...SURFACE_FLUX_PALETTE[index],
          color_name: SURFACE_FLUX_PALETTE[index].name,
        })),
        z_equals_one_meaning: "expected-equivalent neutral-reference exposure; not a leaf target",
      }];
    })),
  ]));
  for (const metric of Object.values(legends)) {
    for (const legend of Object.values(metric)) {
      for (const anchor of legend.anchors) delete anchor.name;
    }
  }
  const metadata = {
    schema_id: "fspm-optics.surface-flux-display", schema_version: 1,
    availability: "available", run_id: "a".repeat(32), profile_id: PROFILE_ID,
    quality_family: qualityFamily, selected_calibration_profile: profilePayload,
    calibration_provenance: {
      report_schema_id: "fspm-optics.surface-flux-calibration-report", report_schema_version: 1,
      original_report_outcome: "complete_fail", original_cross_quality_convergence_rewritten: false,
      repository_revision: "3a35cc3a8d143e624a468049344ed15676d791e8",
      configuration_sha256: "d388d6293a94102232f5211dd8bb0b942b401ea30794a63c9f36d8920ae4d9b0",
      source_model_id: "neutral-uniform-upper-hemisphere-v1",
      source_definition_sha256: "cfe8b701186ef8c2cd49ae8d8e9783968353cbc9383e47e3674b8909bb3e6530",
      calibration_scene_sha256: "a3bf55f2e2755ee74a28c6285d8b2408ead18ca3c416487988b238debe784d32",
      topology_sha256: "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09",
      receivers_sha256: "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc",
      material_plan_sha256: "c5d829ecdbde2b06602468e187fd75c6314f55e671b2d5529289d40adb4ccff6",
      source_scope: "open-boundary uniform upper-hemisphere neutral field",
    },
    reference,
    display_equation: {
      formula: "z = q / (beta * R)", q_authority: "Phase 27G-C Float64",
      z_equals_one_meaning: "expected-equivalent neutral-reference exposure; not a leaf target",
      transport_correction: false,
    },
    metrics: {
      available: ["incident_par", "absorbed_par"],
      incident_par: "blue + green + orange + red incident density",
      absorbed_par: "blue + green + orange + red absorbed density", far_red_excluded: true,
    },
    palette: {
      palette_id: "surface-flux-blue-to-red-8-v1",
      colors: SURFACE_FLUX_PALETTE.map((value) => ({ ...value })),
      front_anchors_z: [...FRONT_ANCHORS], back_anchors_z: [...BACK_ANCHORS],
      shared_across_metrics_systems_and_quality_families: true,
      per_run_extrema_normalization: false,
    },
    legends,
    scientific_artifact_boundary: {
      aggregation_schema_id: "fspm-optics.fspm-surface-light-aggregation",
      aggregation_schema_version: 1, aggregation_metadata_sha256: HASH,
      patch_float64_artifact: {
        role: "fspm_patch_surface_light",
        path: "fspm-aggregation/patch-surface-light.v1.f64le.bin",
        media_type: "application/octet-stream",
        byte_length: counts.patches * 232, sha256: HASH,
        row_count: counts.patches, stride_bytes: 232,
      },
      room_summary_artifact: {
        role: "fspm_room_surface_light_summary",
        path: "fspm-aggregation/room-summary.v1.json",
        media_type: "application/json", byte_length: 100, sha256: HASH,
      },
      transport_schema_id: "fspm-optics.juvenile-multi-plant-five-band-receivers",
      transport_schema_version: 2, transport_metadata_sha256: HASH,
      compact_receiver_index_sha256: HASH,
      material_coefficient_authorities: ["blue", "green", "orange", "red", "far_red"].map(
        (band_id) => ({
          band_id, material_sha256: HASH, material_provenance_sha256: HASH,
          raw_receiver_sha256: HASH,
        }),
      ),
      raw_float64_values_modified: false, far_red_read_into_display_values: false,
    },
    topology: {
      canonical_profile_id: PROFILE_ID,
      topology_sha256: "b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09",
      receivers_sha256: "e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc",
      counts, local_patches_per_plant: 192,
      global_patch_equation: "global_patch_index = 192 * plant_index + local_patch_index",
      instance_mapping: "instance_id equals canonical plant_index",
      front_back_selection: "fragment gl_FrontFacing selects front or back",
      layout_plan_hash: HASH, ordering: "Y-major/X-minor plants; plant-major canonical patches",
    },
    display_artifact: {
      filename: "surface-flux/patch-values.v1.f32le.bin", byte_length: counts.patches * 16,
      sha256: HASH, component_type: "float32", byte_order: "little-endian",
      stride_bytes: 16, row_count: counts.patches,
      record_layout: "front_incident_par, back_incident_par, front_absorbed_par, back_absorbed_par",
      texture_layout: {
        format: "RGBA32F", width: 192, height: plants, texel_count: counts.patches,
        bounded_maximum_plant_count: 625,
      },
      display_only_float32_derivative: true,
    },
    failure_policy: {
      invalid_or_unavailable: "neutral plant material", clear_stale_gpu_resources: true,
      partial_scientific_coloring_allowed: false,
    },
  };
  const context = {
    scene: {
      schema_id: "fspm-optics.juvenile-rex-run-scene",
      schema_version: 1,
      viewer_resource_version: LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION,
      run: { run_id: "a".repeat(32) },
      profile: {
        profile_id: PROFILE_ID,
        sampling_profile_id: LEGACY_SAMPLING_PROFILE,
      },
      natural_fit: { plant_count: plants, plan_hash: HASH },
      surface_flux: {
        availability: "available",
        metadata: {
          filename: "surface-flux/metadata.v1.json",
          byte_length: 1,
          sha256: HASH,
        },
      },
    },
    profile: {
      schema_id: "fspm-optics.juvenile-rex-viewer-profile",
      schema_version: 1,
      profile_id: PROFILE_ID,
      sampling_profile_id: LEGACY_SAMPLING_PROFILE,
      counts: { patches: 192 },
    },
    identity: {
      schema_id: "fspm-optics.juvenile-rex-identity-map",
      schema_version: 1,
      profile_id: PROFILE_ID,
      sampling_profile_id: LEGACY_SAMPLING_PROFILE,
      patch_ids: Array.from({ length: 192 }, (_, index) => `patch-${index}`),
      face_to_patch: Array.from({ length: 1920 }, (_, index) => Math.floor(index / 10)),
      patch_to_leaf: Array.from({ length: 192 }, (_, index) => Math.floor(index / 16)),
      receiver_to_patch: Array.from({ length: 384 }, (_, index) => Math.floor(index / 2)),
      receiver_side: Array.from({ length: 384 }, (_, index) => index % 2 ? "back" : "front"),
    },
  };
  return { context, metadata };
}
