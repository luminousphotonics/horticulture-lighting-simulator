export const PROFILE_ID = "rex_juvenile_preheading_12leaf_v1";
export const SCENE_SCHEMA = "fspm-optics.juvenile-rex-run-scene";
export const PROFILE_SCHEMA = "fspm-optics.juvenile-rex-viewer-profile";
export const IDENTITY_SCHEMA = "fspm-optics.juvenile-rex-identity-map";
export const VIEWER_RESOURCE_VERSION = "phase27g-d6-target-coverage-v1";
export const PPFD_HEATMAP_VIEWER_RESOURCE_VERSION = "phase27h-g-ppfd-heatmap-v1";
export const MOUNTING_VIEWER_RESOURCE_VERSION = "phase27h-b-mounting-height-v1";
export const D4_VIEWER_RESOURCE_VERSION = "phase27g-d4-viewer-leaf-materials-v1";
export const D3_VIEWER_RESOURCE_VERSION = "phase27g-d3b-sampling-profile-v1";
export const LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION = "phase27g-d2-surface-flux-v1";
export const OPTIMIZED_SAMPLING_PROFILE =
  "rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1";
export const LEGACY_SAMPLING_PROFILE =
  "rex_juvenile_surface_sampling_uv_quarter_4x4_v1";

const SAMPLING_PROFILES = Object.freeze([
  OPTIMIZED_SAMPLING_PROFILE,
  LEGACY_SAMPLING_PROFILE,
]);

const SUPPORTED_VIEWER_RESOURCE_VERSIONS = Object.freeze([
  VIEWER_RESOURCE_VERSION,
  PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
  MOUNTING_VIEWER_RESOURCE_VERSION,
  D4_VIEWER_RESOURCE_VERSION,
  D3_VIEWER_RESOURCE_VERSION,
  LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION,
]);

const PROFILE_SCHEMA_VERSIONS_BY_RESOURCE = Object.freeze({
  [VIEWER_RESOURCE_VERSION]: Object.freeze([3]),
  [PPFD_HEATMAP_VIEWER_RESOURCE_VERSION]: Object.freeze([3]),
  [MOUNTING_VIEWER_RESOURCE_VERSION]: Object.freeze([3]),
  [D4_VIEWER_RESOURCE_VERSION]: Object.freeze([3]),
  [D3_VIEWER_RESOURCE_VERSION]: Object.freeze([2]),
  [LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION]: Object.freeze([1, 2]),
});

const COUNTS = Object.freeze({
  leaves: 12,
  faces: 1920,
  patches: 192,
  receivers: 384,
});

export function validateRunScene(scene) {
  assertObject(scene, "scene manifest");
  if (scene.schema_id !== SCENE_SCHEMA || ![1, 2, 3, 4].includes(scene.schema_version)) {
    throw new Error("Scene schema is incompatible.");
  }
  if (!SUPPORTED_VIEWER_RESOURCE_VERSIONS.includes(scene.viewer_resource_version)) {
    throw new Error("Viewer resource version is incompatible.");
  }
  const fields = [
    "aligned_simulation_room", "camera_bounds", "fixtures", "natural_fit", "plant_bounds", "plant_instances", "profile",
    "projected_footprint_bounds", "reference_plane", "requested_room", "room_bounds",
    "run", "schema_id", "schema_version", "viewer_resource_version",
  ];
  if (scene.surface_flux !== undefined) fields.push("surface_flux");
  if (scene.mounting_height !== undefined) fields.push("mounting_height");
  if (scene.ppfd_heatmap !== undefined) fields.push("ppfd_heatmap");
  if (scene.active_domain !== undefined) fields.push("active_domain");
  if (scene.requested_orientation !== undefined) fields.push("requested_orientation");
  if (Object.keys(scene).sort().join(",") !== fields.sort().join(",")) {
    throw new Error("Scene field inventory is incompatible.");
  }
  if (!scene.run || !/^[0-9a-f]{32}$/.test(scene.run.run_id)
      || !["proposed", "conventional", "hps"].includes(scene.run.system_id)) {
    throw new Error("Scene run identity is incompatible.");
  }
  const runFields = ["run_id", "system_id"];
  if (scene.run.information !== undefined) runFields.push("information");
  if (Object.keys(scene.run).sort().join(",") !== runFields.sort().join(",")) {
    throw new Error("Scene run-information inventory is incompatible.");
  }
  if (scene.run.information !== undefined) {
    const control = scene.run.information?.proposed_control;
    const source = scene.run.information?.proposed_source;
    const layout = scene.run.information?.proposed_layout;
    const supportedModes = ["basis_matrix_optimized", "uniform_module_dimming"];
    if (scene.run.system_id !== "proposed"
        || !control || !supportedModes.includes(control.mode)
        || typeof control.label !== "string" || control.label.length === 0
        || control.basis_matrix_solver_enabled
          !== (control.mode === "basis_matrix_optimized")) {
      throw new Error("Scene Proposed control information is incompatible.");
    }
    if (source !== undefined) {
      const supportedSources = ["native_smd", "cob_source_shape_surrogate"];
      if (!supportedSources.includes(source?.source_mode)
          || typeof source.classification !== "string"
          || !source.emitter_geometry
          || source.emitter_geometry.centered_on_existing_module_axis !== true
          || source.completed_aperture_characterization
            ?.traced_ppfd_post_scale !== false) {
        throw new Error("Scene Proposed source information is incompatible.");
      }
      if (source.source_mode === "cob_source_shape_surrogate") {
        const active = source.viewer_active_emitter;
        const angular = source.authenticated_angular_law;
        if (source.classification !== "cob_source_shape_surrogate"
            || source.emitter_geometry.shape !== "centered_circular_les"
            || source.emitter_geometry.diameter_m !== 0.022
            || !angular?.input?.sha256
            || !angular?.normalization?.identity_sha256
            || active?.smd_emitter_plane_active !== false
            || active?.fixture_body_transforms_changed !== false
            || active?.fixture_occlusion_classification_changed !== false
            || !Array.isArray(active?.modules) || active.modules.length === 0) {
          throw new Error("Scene COB source information is incompatible.");
        }
      }
    }
    if (layout !== undefined) {
      const patterns = {
        full: "centered_square_full_v1",
        reduced_one_ring: "centered_square_reduced_one_ring_v1",
      };
      if (!Object.hasOwn(patterns, layout?.ring_mode)
          || layout.module_pattern_id !== patterns[layout.ring_mode]) {
        throw new Error("Scene Proposed ring information is incompatible.");
      }
    }
  }
  const fixtures = scene.fixtures;
  const fixtureFields = [
    "asset_group_count", "authoritative_layout_sha256", "catalog",
    "catalog_byte_length", "catalog_sha256", "fixture_count",
    "fixture_plan_sha256",
  ];
  if ([3, 4].includes(scene.schema_version)) {
    fixtureFields.push("mounting_height_sha256");
  }
  if (!fixtures || Object.keys(fixtures).sort().join(",")
      !== fixtureFields.sort().join(",")) {
    throw new Error("Scene fixture field inventory is incompatible.");
  }
  if (fixtures?.catalog !== "fixtures/catalog.v1.json"
      || !Number.isSafeInteger(fixtures.catalog_byte_length)
      || fixtures.catalog_byte_length <= 0
      || !Number.isSafeInteger(fixtures.fixture_count) || fixtures.fixture_count <= 0
      || !Number.isSafeInteger(fixtures.asset_group_count)
      || fixtures.asset_group_count <= 0
      || fixtures.asset_group_count > fixtures.fixture_count) {
    throw new Error("Scene fixture catalog reference is incompatible.");
  }
  assertSha256(fixtures.catalog_sha256, "fixture catalog");
  assertSha256(fixtures.authoritative_layout_sha256, "fixture layout");
  assertSha256(fixtures.fixture_plan_sha256, "fixture plan");
  if ([3, 4].includes(scene.schema_version)) {
    const expectedResources = scene.schema_version === 4
      ? [VIEWER_RESOURCE_VERSION, PPFD_HEATMAP_VIEWER_RESOURCE_VERSION]
      : [MOUNTING_VIEWER_RESOURCE_VERSION];
    if (!expectedResources.includes(scene.viewer_resource_version)) {
      throw new Error("Mounting-height scene resource version is incompatible.");
    }
    assertSha256(fixtures.mounting_height_sha256, "mounting height");
    validateMountingHeight(scene.mounting_height);
  } else if (scene.mounting_height !== undefined
      || [
        VIEWER_RESOURCE_VERSION,
        PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
        MOUNTING_VIEWER_RESOURCE_VERSION,
      ]
        .includes(scene.viewer_resource_version)) {
    throw new Error("Historical scene cannot carry mounting-height provenance.");
  }
  if (scene.schema_version === 4) {
    if (scene.ppfd_heatmap === undefined) {
      throw new Error("Current scene PPFD heatmap source is missing.");
    }
    const hasTargetCoverage = scene.ppfd_heatmap?.target_coverage !== undefined;
    if ((scene.viewer_resource_version === VIEWER_RESOURCE_VERSION) !== hasTargetCoverage) {
      throw new Error("Target Coverage scene resource version is incompatible.");
    }
  } else if (scene.ppfd_heatmap !== undefined) {
    throw new Error("Historical scene cannot carry a PPFD heatmap source.");
  }
  const room = scene.requested_room;
  if (![room?.length_ft, room?.width_ft].every(
    (value) => Number.isFinite(value) && value > 0,
  ) || !Number.isFinite(room.length_m) || !Number.isFinite(room.width_m)
      || Math.abs(room.length_m - room.length_ft * 0.3048) > 1e-12
      || Math.abs(room.width_m - room.width_ft * 0.3048) > 1e-12) {
    throw new Error("Scene requested room is incompatible.");
  }
  const alignedRoom = scene.aligned_simulation_room;
  const frame = alignedRoom?.coordinate_frame;
  const axesSwapped = room.width_m > room.length_m;
  const expectedAlignedLengthM = axesSwapped ? room.width_m : room.length_m;
  const expectedAlignedWidthM = axesSwapped ? room.length_m : room.width_m;
  const expectedRotationDegrees = axesSwapped ? -90 : 0;
  const expectedRotationMatrix = axesSwapped
    ? [0, 1, 0, -1, 0, 0, 0, 0, 1]
    : [1, 0, 0, 0, 1, 0, 0, 0, 1];
  if (!Number.isFinite(alignedRoom?.length_m) || !Number.isFinite(alignedRoom?.width_m)
      || alignedRoom.length_m !== expectedAlignedLengthM
      || alignedRoom.width_m !== expectedAlignedWidthM
      || alignedRoom.long_axis !== "x"
      || frame?.policy_id !== "requested_room_to_long_axis_x_rigid_rotation_v1"
      || frame?.determinant !== 1 || frame?.preserves_z !== true
      || frame?.axes_swapped !== axesSwapped
      || frame?.rotation_degrees_about_z !== expectedRotationDegrees
      || JSON.stringify(frame?.rotation_matrix_row_major)
        !== JSON.stringify(expectedRotationMatrix)
      || JSON.stringify(frame?.requested_room_m) !== JSON.stringify({
        length_x_m: room.length_m, width_y_m: room.width_m,
      })
      || JSON.stringify(frame?.aligned_simulation_room_m) !== JSON.stringify({
        length_x_m: alignedRoom.length_m, width_y_m: alignedRoom.width_m,
      })
      || JSON.stringify(frame?.translation_m) !== JSON.stringify([0, 0, 0])
      || frame?.position_rule !== "rotation_then_translation"
      || frame?.direction_rule !== "rotation_only") {
    throw new Error("Scene aligned simulation room is incompatible.");
  }
  if (scene.active_domain !== undefined) {
    const domain = scene.active_domain;
    const aisleM = domain?.enabled === true ? 0.6096 : 0;
    const activeLength = room.length_m - 2 * aisleM;
    const activeWidth = room.width_m - 2 * aisleM;
    const alignedActiveLength = Math.max(activeLength, activeWidth);
    const alignedActiveWidth = Math.min(activeLength, activeWidth);
    const bounds = domain?.active_bounds_aligned_m;
    const areas = domain?.areas_m2;
    const close = (actual, expected) => (
      typeof actual === "number"
      && Number.isFinite(actual)
      && Math.abs(actual - expected) <= 1e-12
    );
    if (domain?.schema_id !== "fspm-optics.active-room-domain"
        || domain?.schema_version !== 1
        || domain?.policy_id !== "centered_fixed_2ft_perimeter_aisle_v1"
        || typeof domain?.enabled !== "boolean"
        || domain?.aisle?.width_m_per_wall !== aisleM
        || domain?.aisle?.width_ft_per_wall !== (domain.enabled ? 2 : 0)
        || domain?.outer_requested_m?.length !== room.length_m
        || domain?.outer_requested_m?.width !== room.width_m
        || domain?.outer_aligned_m?.length_x !== alignedRoom.length_m
        || domain?.outer_aligned_m?.width_y !== alignedRoom.width_m
        || !close(domain?.active_requested_m?.length, activeLength)
        || !close(domain?.active_requested_m?.width, activeWidth)
        || !close(domain?.active_aligned_m?.length_x, alignedActiveLength)
        || !close(domain?.active_aligned_m?.width_y, alignedActiveWidth)
        || !close(bounds?.min_x, -alignedActiveLength / 2)
        || !close(bounds?.max_x, alignedActiveLength / 2)
        || !close(bounds?.min_y, -alignedActiveWidth / 2)
        || !close(bounds?.max_y, alignedActiveWidth / 2)
        || !close(areas?.outer_room, room.length_m * room.width_m)
        || !close(areas?.active_grow, activeLength * activeWidth)
        || domain?.centered !== true
        || JSON.stringify(domain?.coordinate_frame) !== JSON.stringify(frame)) {
      throw new Error("Scene active domain is incompatible.");
    }
    assertSha256(domain.identity_sha256, "active domain");
  }
  if (scene.requested_orientation !== undefined) {
    validateRequestedOrientation(scene.requested_orientation, scene, frame);
  }
  assertObject(scene.profile, "scene profile");
  if (scene.profile.profile_id !== PROFILE_ID) {
    throw new Error("Scene profile is not the approved juvenile profile.");
  }
  const samplingProfile = resolveSceneSamplingProfile(scene);
  if (scene.schema_version === 1
      && scene.viewer_resource_version !== LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION) {
    throw new Error("Historical scene resource version is incompatible.");
  }
  if (samplingProfile === OPTIMIZED_SAMPLING_PROFILE
      && ![
        VIEWER_RESOURCE_VERSION,
        PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
        MOUNTING_VIEWER_RESOURCE_VERSION,
        D4_VIEWER_RESOURCE_VERSION,
        D3_VIEWER_RESOURCE_VERSION,
      ]
        .includes(scene.viewer_resource_version)) {
    throw new Error("Optimized scene resource version is incompatible.");
  }
  if (scene.surface_flux !== undefined) {
    const filename = scene.surface_flux?.metadata?.filename;
    const compatible = samplingProfile === OPTIMIZED_SAMPLING_PROFILE
      ? filename === "surface-flux/metadata.v3.json"
      : samplingProfile === LEGACY_SAMPLING_PROFILE
        && ["surface-flux/metadata.v1.json", "surface-flux/metadata.v2.json"]
          .includes(filename);
    if (!compatible) {
      throw new Error(
        "Surface-flux calibration does not cover this receiver sampling profile.",
      );
    }
  }
  if (scene.profile.manifest !== `profiles/${PROFILE_ID}/profile.v1.json`) {
    throw new Error("Scene profile manifest path is incompatible.");
  }
  assertSha256(scene.profile.manifest_sha256, "profile manifest");
  const fit = scene.natural_fit;
  assertObject(fit, "Natural-fit reference");
  if (fit.artifact_role !== "natural_fit_layout"
      || fit.profile_id !== PROFILE_ID
      || fit.policy_id !== "natural_fit_0p40m_sampling_v1"
      || fit.ordering !== "Y-major/X-minor"
      || !Number.isSafeInteger(fit.plant_count) || fit.plant_count <= 0) {
    throw new Error("Scene Natural-fit reference is incompatible.");
  }
  assertSha256(fit.artifact_sha256, "Natural-fit artifact");
  assertSha256(fit.plan_hash, "Natural-fit plan");
  const instances = scene.plant_instances;
  if (!Array.isArray(instances?.plant_ids)
      || instances.plant_ids.length !== fit.plant_count
      || instances.plant_ids.some((value) => typeof value !== "string" || !value)
      || new Set(instances.plant_ids).size !== fit.plant_count) {
    throw new Error("Scene ordered plant identities are incompatible.");
  }
  const artifact = instances.instance_translations;
  if (artifact?.filename !== "instances.f32le.bin"
      || artifact.record_layout !== "translation_x_y_z"
      || artifact.coordinate_space !== "viewer right-handed meters, Y-up"
      || artifact.scientific_to_viewer !== "(x, y, z) -> (x, z, -y)"
      || artifact.component_type !== "float32"
      || artifact.byte_order !== "little-endian"
      || artifact.stride_bytes !== 12
      || artifact.count !== fit.plant_count
      || artifact.byte_length !== fit.plant_count * 12) {
    throw new Error("Scene instance translation contract is incompatible.");
  }
  assertSha256(artifact.sha256, "instance translations");
  assertBounds(scene.camera_bounds, "camera bounds", "xyz");
  assertBounds(scene.plant_bounds, "plant bounds", "xyz");
  assertBounds(scene.projected_footprint_bounds, "footprint bounds", "xz");
  assertBounds(scene.room_bounds, "room bounds", "xz");
  const expectedRoomBounds = [-alignedRoom.length_m / 2, -alignedRoom.width_m / 2,
    alignedRoom.length_m / 2, alignedRoom.width_m / 2];
  if (JSON.stringify([
    ...scene.room_bounds.minimum_xz, ...scene.room_bounds.maximum_xz,
  ]) !== JSON.stringify(expectedRoomBounds)) {
    throw new Error("Scene room bounds do not match the aligned simulation room.");
  }
  if (!Array.isArray(scene.reference_plane?.vertices_xyz)
      || scene.reference_plane.vertices_xyz.length !== 4
      || scene.reference_plane.coordinate_system !== "viewer Y-up"
      || scene.reference_plane.vertices_xyz.some(
        (vertex) => !Array.isArray(vertex) || vertex.length !== 3
          || vertex.some((item) => !Number.isFinite(item)),
      )) {
    throw new Error("Scene reference-plane geometry is incompatible.");
  }
  const [minX, minZ, maxX, maxZ] = expectedRoomBounds;
  const expectedPlane = [[minX, 0, minZ], [maxX, 0, minZ],
    [maxX, 0, maxZ], [minX, 0, maxZ]];
  if (JSON.stringify(scene.reference_plane.vertices_xyz) !== JSON.stringify(expectedPlane)) {
    throw new Error("Scene reference plane does not match the aligned simulation room.");
  }
  return scene;
}

function validateRequestedOrientation(presentation, scene, frame) {
  assertObject(presentation, "requested orientation");
  const fields = [
    "canonical_bundle_identity_sha256", "canonical_case_id",
    "canonical_display_room_ft", "coordinate_frame",
    "fspm_target_tolerance_umol_m2_s", "heatmap_counterclockwise_quarter_turns",
    "presentation_identity_sha256", "requested_room_ft",
    "scalar_metrics_invariant", "schema_id", "schema_version",
    "simulation_to_requested_rotation_degrees_about_z",
    "viewer_global_rotation_degrees_about_y",
  ];
  if (presentation.derived_playback_identity_sha256 !== undefined) {
    fields.push("derived_playback_identity_sha256");
  }
  if (presentation.target_adjustment !== undefined) fields.push("target_adjustment");
  if (presentation.lighting_target_mode !== undefined) {
    fields.push("lighting_target_mode");
  }
  if (presentation.fspm_tolerance_presentation !== undefined) {
    fields.push("fspm_tolerance_presentation");
  }
  if (Object.keys(presentation).sort().join(",") !== fields.sort().join(",")) {
    throw new Error("Requested-orientation field inventory is incompatible.");
  }
  const schemaVersion = presentation.schema_version;
  const tolerance = presentation.fspm_target_tolerance_umol_m2_s;
  const targetAdjusted = presentation.derived_playback_identity_sha256 !== undefined;
  const targetAdjustment = presentation.target_adjustment;
  const tolerancePresentation = presentation.fspm_tolerance_presentation;
  if (![2, 3, 4].includes(schemaVersion)
      || !Number.isFinite(tolerance) || tolerance <= 0
      || (targetAdjustment !== undefined) !== targetAdjusted
      || (schemaVersion === 4) !== (tolerancePresentation !== undefined)
      || (schemaVersion !== 4 && tolerance !== 75)
      || (schemaVersion === 4 && tolerance === 75)
      || (schemaVersion === 2 && presentation.lighting_target_mode !== undefined)
      || (schemaVersion === 3
        && presentation.lighting_target_mode !== "target_capped")
      || (presentation.lighting_target_mode !== undefined
        && presentation.lighting_target_mode !== "target_capped")) {
    throw new Error("Requested-orientation presentation schema is incompatible.");
  }
  if (targetAdjusted) {
    validateTargetAdjustmentBinding(targetAdjustment, presentation);
  }
  if (schemaVersion === 4) {
    validateFspmTolerancePresentation(tolerancePresentation, presentation);
  }
  const requestedLength = presentation.requested_room_ft?.length;
  const requestedWidth = presentation.requested_room_ft?.width;
  const displayLength = presentation.canonical_display_room_ft?.length;
  const displayWidth = presentation.canonical_display_room_ft?.width;
  if (presentation.schema_id !== "fspm-optics.requested-orientation-playback"
      || typeof presentation.canonical_case_id !== "string"
      || presentation.canonical_case_id.length === 0
      || !/^[0-9a-f]{64}$/.test(presentation.canonical_bundle_identity_sha256)
      || !/^[0-9a-f]{64}$/.test(presentation.presentation_identity_sha256)
      || (presentation.derived_playback_identity_sha256 !== undefined
        && !/^[0-9a-f]{64}$/.test(presentation.derived_playback_identity_sha256))
      || JSON.stringify(presentation.coordinate_frame) !== JSON.stringify(frame)
      || !Number.isFinite(requestedLength) || !Number.isFinite(requestedWidth)
      || requestedLength <= 0 || requestedWidth <= 0
      || Math.max(requestedLength, requestedWidth) !== displayLength
      || Math.min(requestedLength, requestedWidth) !== displayWidth
      || displayLength !== scene.requested_room.length_ft
      || displayWidth !== scene.requested_room.width_ft
      || frame.axes_swapped !== false
      || presentation.simulation_to_requested_rotation_degrees_about_z !== 0
      || presentation.viewer_global_rotation_degrees_about_y !== 0
      || presentation.heatmap_counterclockwise_quarter_turns !== 0
      || typeof presentation.scalar_metrics_invariant !== "boolean") {
    throw new Error("Requested-orientation contract is incompatible.");
  }
}

function validateTargetAdjustmentBinding(adjustment, presentation) {
  assertObject(adjustment, "requested-orientation target adjustment");
  const supportedSchemas = [
    "fspm-optics.precomputed-linear-target-adjustment",
    "fspm-optics.precomputed-authenticated-target-capped-playback",
  ];
  if (!supportedSchemas.includes(adjustment.schema_id)
      || adjustment.schema_version !== 1
      || adjustment.base_bundle_identity_sha256
        !== presentation.canonical_bundle_identity_sha256
      || adjustment.derived_playback_identity_sha256
        !== presentation.derived_playback_identity_sha256
      || (adjustment.schema_id
        === "fspm-optics.precomputed-authenticated-target-capped-playback")
        !== (presentation.lighting_target_mode === "target_capped")) {
    throw new Error("Requested-orientation target adjustment is incompatible.");
  }
  assertSha256(adjustment.base_bundle_identity_sha256, "target-adjustment bundle");
  assertSha256(
    adjustment.derived_playback_identity_sha256,
    "target-adjustment derivation",
  );
}

function validateFspmTolerancePresentation(metadata, presentation) {
  assertObject(metadata, "FSPM tolerance presentation");
  const fields = [
    "authenticated_bundle_identity_sha256", "classification_metadata_only",
    "derivation_identity_sha256", "fspm_target_tolerance_umol_m2_s",
    "schema_id", "schema_version", "source_derived_playback_identity_sha256",
    "stage_a_transport_recomputed",
  ];
  if (Object.keys(metadata).sort().join(",") !== fields.sort().join(",")
      || metadata.schema_id
        !== "fspm-optics.precomputed-fspm-tolerance-presentation"
      || metadata.schema_version !== 1
      || metadata.authenticated_bundle_identity_sha256
        !== presentation.canonical_bundle_identity_sha256
      || metadata.source_derived_playback_identity_sha256
        !== (presentation.derived_playback_identity_sha256 ?? null)
      || metadata.fspm_target_tolerance_umol_m2_s
        !== presentation.fspm_target_tolerance_umol_m2_s
      || metadata.classification_metadata_only !== true
      || metadata.stage_a_transport_recomputed !== false) {
    throw new Error("FSPM tolerance presentation is incompatible.");
  }
  assertSha256(
    metadata.authenticated_bundle_identity_sha256,
    "FSPM tolerance authenticated bundle",
  );
  assertSha256(metadata.derivation_identity_sha256, "FSPM tolerance derivation");
  if (metadata.source_derived_playback_identity_sha256 !== null) {
    assertSha256(
      metadata.source_derived_playback_identity_sha256,
      "FSPM tolerance source derivation",
    );
  }
}

export function validateMountingHeight(mounting) {
  assertObject(mounting, "mounting height");
  const fields = [
    "definition", "emitting_aperture_plane_z_m", "input_unit",
    "meters_per_inch", "mounting_height_in", "mounting_height_m",
    "reference_plane_z_m", "room_ceiling_z_m", "schema_id", "schema_version",
  ];
  const canonical = canonicalMountingValues(mounting.mounting_height_in);
  if (Object.keys(mounting).sort().join(",") !== fields.sort().join(",")
      || mounting.schema_id !== "fspm-optics.mounting-height-provenance"
      || mounting.schema_version !== 1 || mounting.input_unit !== "inch"
      || mounting.meters_per_inch !== 0.0254
      || mounting.definition
        !== "emitting_aperture_plane_to_receiver_reference_plane"
      || !Number.isFinite(mounting.mounting_height_in)
      || mounting.mounting_height_in <= 0
      || mounting.reference_plane_z_m !== 0.005
      || mounting.room_ceiling_z_m !== 3.048
      || mounting.mounting_height_m !== canonical.mountingHeightM
      || mounting.emitting_aperture_plane_z_m !== canonical.apertureZM
      || mounting.emitting_aperture_plane_z_m >= mounting.room_ceiling_z_m) {
    throw new Error("Mounting-height provenance is incompatible.");
  }
}

function canonicalMountingValues(heightIn) {
  if (!Number.isFinite(heightIn) || heightIn <= 0) {
    return { mountingHeightM: Number.NaN, apertureZM: Number.NaN };
  }
  const inches = decimalParts(heightIn);
  const mounting = {
    coefficient: inches.coefficient * 254n,
    scale: inches.scale + 4,
  };
  const aperture = addDecimalParts(mounting, {
    coefficient: 5n,
    scale: 3,
  });
  return {
    mountingHeightM: decimalPartsToNumber(mounting),
    apertureZM: decimalPartsToNumber(aperture),
  };
}

function decimalParts(value) {
  const match = String(value).match(/^(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
  if (!match) throw new Error("Mounting-height decimal value is incompatible.");
  const fraction = match[2] ?? "";
  let coefficient = BigInt(`${match[1]}${fraction}`);
  let scale = fraction.length - Number(match[3] ?? 0);
  if (scale < 0) {
    coefficient *= 10n ** BigInt(-scale);
    scale = 0;
  }
  return { coefficient, scale };
}

function addDecimalParts(left, right) {
  const scale = Math.max(left.scale, right.scale);
  return {
    coefficient: left.coefficient * 10n ** BigInt(scale - left.scale)
      + right.coefficient * 10n ** BigInt(scale - right.scale),
    scale,
  };
}

function decimalPartsToNumber({ coefficient, scale }) {
  const digits = coefficient.toString().padStart(scale + 1, "0");
  if (scale === 0) return Number(digits);
  return Number(`${digits.slice(0, -scale)}.${digits.slice(-scale)}`);
}

export function validateSurfaceFluxSceneReference(scene) {
  assertObject(scene?.surface_flux, "surface-flux scene reference");
  const samplingProfile = resolveSceneSamplingProfile(scene);
  const surfaceMetadata = scene.surface_flux.metadata;
  const allowedFilenames = [
    VIEWER_RESOURCE_VERSION,
    PPFD_HEATMAP_VIEWER_RESOURCE_VERSION,
    MOUNTING_VIEWER_RESOURCE_VERSION,
  ].includes(scene.viewer_resource_version)
    ? [
      "surface-flux/metadata.v1.json",
      "surface-flux/metadata.v2.json",
      "surface-flux/metadata.v3.json",
    ]
    : scene.viewer_resource_version === D3_VIEWER_RESOURCE_VERSION
      ? ["surface-flux/metadata.v2.json"]
      : scene.viewer_resource_version === LEGACY_SURFACE_FLUX_VIEWER_RESOURCE_VERSION
        ? ["surface-flux/metadata.v1.json"]
        : [];
  const samplingCompatible = samplingProfile === OPTIMIZED_SAMPLING_PROFILE
    ? surfaceMetadata?.filename === "surface-flux/metadata.v3.json"
    : samplingProfile === LEGACY_SAMPLING_PROFILE
      && ["surface-flux/metadata.v1.json", "surface-flux/metadata.v2.json"]
        .includes(surfaceMetadata?.filename);
  if (!samplingCompatible
      || scene.surface_flux.availability !== "available"
      || Object.keys(scene.surface_flux).sort().join(",") !== "availability,metadata"
      || !allowedFilenames.includes(surfaceMetadata?.filename)
      || !Number.isSafeInteger(surfaceMetadata.byte_length)
      || surfaceMetadata.byte_length <= 0) {
    throw new Error("Scene surface-flux reference is incompatible.");
  }
  assertSha256(surfaceMetadata.sha256, "surface-flux metadata");
  return surfaceMetadata;
}

export function parseInstanceTranslations(bytes, artifact) {
  if (!(bytes instanceof ArrayBuffer)) {
    throw new Error("Instance translation artifact must be an ArrayBuffer.");
  }
  if (artifact?.component_type !== "float32"
      || artifact.byte_order !== "little-endian"
      || artifact.record_layout !== "translation_x_y_z"
      || artifact.stride_bytes !== 12
      || !Number.isSafeInteger(artifact.count) || artifact.count <= 0
      || artifact.byte_length !== artifact.count * artifact.stride_bytes
      || bytes.byteLength !== artifact.byte_length) {
    throw new Error("Instance translation length, count, type, or stride is invalid.");
  }
  const view = new DataView(bytes);
  const values = new Float32Array(artifact.count * 3);
  for (let index = 0; index < values.length; index += 1) {
    values[index] = view.getFloat32(index * 4, true);
    if (!Number.isFinite(values[index])) {
      throw new Error("Instance translations contain a non-finite value.");
    }
  }
  return Object.freeze({ count: artifact.count, values });
}

export function validateDisplayBounds(scene, geometry, translations) {
  const positions = geometry?.attributes?.POSITION;
  if (!(positions instanceof Float32Array) || positions.length === 0
      || translations?.count !== scene.natural_fit.plant_count) {
    throw new Error("Display bounds inputs are incompatible.");
  }
  const localMin = [Infinity, Infinity, Infinity];
  const localMax = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < positions.length; index += 3) {
    for (let axis = 0; axis < 3; axis += 1) {
      localMin[axis] = Math.min(localMin[axis], positions[index + axis]);
      localMax[axis] = Math.max(localMax[axis], positions[index + axis]);
    }
  }
  const translationMin = [Infinity, Infinity, Infinity];
  const translationMax = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < translations.values.length; index += 3) {
    for (let axis = 0; axis < 3; axis += 1) {
      translationMin[axis] = Math.min(translationMin[axis], translations.values[index + axis]);
      translationMax[axis] = Math.max(translationMax[axis], translations.values[index + axis]);
    }
  }
  const minimum = localMin.map((value, axis) => value + translationMin[axis]);
  const maximum = localMax.map((value, axis) => value + translationMax[axis]);
  assertNearVector(scene.plant_bounds.minimum_xyz, minimum, "plant minimum");
  assertNearVector(scene.plant_bounds.maximum_xyz, maximum, "plant maximum");
  assertNearVector(scene.projected_footprint_bounds.minimum_xz,
    [minimum[0], minimum[2]], "footprint minimum");
  assertNearVector(scene.projected_footprint_bounds.maximum_xz,
    [maximum[0], maximum[2]], "footprint maximum");
  assertNearVector(scene.camera_bounds.minimum_xyz,
    [scene.room_bounds.minimum_xz[0], 0, scene.room_bounds.minimum_xz[1]],
    "camera minimum");
  assertNearVector(scene.camera_bounds.maximum_xyz,
    [scene.room_bounds.maximum_xz[0], scene.plant_bounds.maximum_xyz[1],
      scene.room_bounds.maximum_xz[1]], "camera maximum");
  if (minimum[0] < scene.room_bounds.minimum_xz[0] - 1e-6
      || minimum[2] < scene.room_bounds.minimum_xz[1] - 1e-6
      || maximum[0] > scene.room_bounds.maximum_xz[0] + 1e-6
      || maximum[2] > scene.room_bounds.maximum_xz[1] + 1e-6) {
    throw new Error("Plant bounds exceed the aligned simulation room.");
  }
  return true;
}

export function validateProfileManifest(profile) {
  assertObject(profile, "profile manifest");
  if (profile.schema_id !== PROFILE_SCHEMA || ![1, 2, 3].includes(profile.schema_version)
      || profile.profile_id !== PROFILE_ID) {
    throw new Error("Profile schema or identity is incompatible.");
  }
  const samplingProfile = resolveProfileSamplingProfile(profile);
  if (!profile.counts
      || Object.entries(COUNTS).some(([name, count]) => profile.counts[name] !== count)) {
    throw new Error("Profile scientific counts are incompatible.");
  }
  const transform = profile.coordinate_transform;
  if (transform?.formula !== "(x, y, z) -> (x, z, -y)"
      || transform.proper_rotation !== true
      || transform.winding_reversed !== false
      || JSON.stringify(transform.matrix_row_major) !== "[1,0,0,0,0,1,0,-1,0]") {
    throw new Error("Profile coordinate conversion metadata is incompatible.");
  }
  if (profile.scientific_coordinate_system !== "right-handed, meters, z-up"
      || profile.viewer_coordinate_system !== "right-handed, meters, Y-up"
      || profile.units !== "meters") {
    throw new Error("Profile unit or coordinate-system metadata is incompatible.");
  }
  if (profile.development?.approximate_days_after_transplant !== 9
      || profile.development?.phenological_boundary !== "BBCH 19/pre-41"
      || profile.calibration_status
        !== "literature_constrained_procedural_reference_geometry") {
    throw new Error("Profile developmental metadata is incompatible.");
  }
  if (profile.schema_version >= 2) {
    const expectedCalibrationStatus = samplingProfile === OPTIMIZED_SAMPLING_PROFILE
      ? "uncalibrated_for_phase27g_d2_surface_flux_display"
      : "legacy_phase27g_d2_surface_flux_calibration_compatible";
    if (profile.sampling_calibration_status !== expectedCalibrationStatus) {
      throw new Error("Profile sampling calibration status is incompatible.");
    }
  }
  if (profile.model_scope?.growth_response_model !== false
      || profile.model_scope?.measured_reconstruction !== false
      || profile.model_scope?.procedural_reference_geometry !== true) {
    throw new Error("Profile model scope is incompatible.");
  }
  const expected = {
    geometry: "geometry.glb",
    identity_map: "identity-map.v1.json",
    receivers: "receivers.f32le.bin",
  };
  if (!profile.artifacts
      || Object.keys(profile.artifacts).sort().join(",")
        !== Object.keys(expected).sort().join(",")) {
    throw new Error("Profile artifact table is incompatible.");
  }
  for (const [role, filename] of Object.entries(expected)) {
    const record = profile.artifacts?.[role];
    if (record?.filename !== filename || !Number.isSafeInteger(record.byte_size)
        || record.byte_size <= 0) {
      throw new Error(`Profile ${role} artifact record is incompatible.`);
    }
    assertSha256(record.sha256, filename);
  }
  return profile;
}

export async function loadValidatedViewerArtifacts(sceneUrl = "./scene.v1.json") {
  const sceneBytes = await fetchBytes(sceneUrl);
  const scene = validateRunScene(decodeJson(sceneBytes, "scene manifest"));
  const profileUrl = new URL(scene.profile.manifest, new URL(sceneUrl, location.href));
  const profileBytes = await fetchBytes(profileUrl);
  await verifyHash(profileBytes, scene.profile.manifest_sha256, "profile manifest");
  const profile = validateProfileManifest(decodeJson(profileBytes, "profile manifest"));
  if (resolveProfileSamplingProfile(profile) !== resolveSceneSamplingProfile(scene)) {
    throw new Error("Scene and profile sampling identities do not agree.");
  }
  validateViewerGeneration(scene, profile);
  const profileBase = new URL("./", profileUrl);
  const geometryBytes = await fetchArtifact(profileBase, profile.artifacts.geometry);
  const identityBytes = await fetchArtifact(profileBase, profile.artifacts.identity_map);
  const receiverBytes = await fetchArtifact(profileBase, profile.artifacts.receivers);
  const instanceBytes = await fetchArtifact(
    new URL("./", new URL(sceneUrl, location.href)),
    {
      filename: scene.plant_instances.instance_translations.filename,
      byte_size: scene.plant_instances.instance_translations.byte_length,
      sha256: scene.plant_instances.instance_translations.sha256,
    },
  );
  const identity = decodeJson(identityBytes, "identity map");
  const geometry = parseScientificGlb(
    geometryBytes, profile.counts.faces, profile.schema_version,
  );
  const translations = parseInstanceTranslations(
    instanceBytes, scene.plant_instances.instance_translations,
  );
  validateDisplayBounds(scene, geometry, translations);
  return {
    scene,
    profile,
    identity,
    geometry,
    receiverBytes,
    translations,
  };
}

export function parseScientificGlb(
  bytes, expectedFaceCount = 1920, profileSchemaVersion = 2,
) {
  if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 28) {
    throw new Error("geometry.glb is truncated.");
  }
  const view = new DataView(bytes);
  if (view.getUint32(0, true) !== 0x46546c67
      || view.getUint32(4, true) !== 2
      || view.getUint32(8, true) !== bytes.byteLength) {
    throw new Error("geometry.glb header is incompatible.");
  }
  const jsonLength = view.getUint32(12, true);
  if (view.getUint32(16, true) !== 0x4e4f534a) {
    throw new Error("geometry.glb JSON chunk is missing.");
  }
  const binaryHeader = 20 + jsonLength;
  if (binaryHeader + 8 > bytes.byteLength
      || view.getUint32(binaryHeader + 4, true) !== 0x004e4942) {
    throw new Error("geometry.glb binary chunk is missing.");
  }
  const binaryLength = view.getUint32(binaryHeader, true);
  const binaryOffset = binaryHeader + 8;
  if (binaryOffset + binaryLength !== bytes.byteLength) {
    throw new Error("geometry.glb binary chunk length is invalid.");
  }
  const jsonText = new TextDecoder().decode(
    new Uint8Array(bytes, 20, jsonLength),
  ).trimEnd();
  const document = JSON.parse(jsonText);
  if (document.animations || document.cameras
      || document.extensionsUsed?.includes("KHR_lights_punctual")
      || document.extensions?.KHR_lights_punctual) {
    throw new Error("geometry.glb contains unsupported scene content.");
  }
  if (document.meshes?.length !== 1 || document.nodes?.length !== 1
      || document.nodes[0].mesh !== 0
      || ["matrix", "rotation", "scale", "translation"]
        .some((name) => document.nodes[0][name] !== undefined)) {
    throw new Error("geometry.glb canonical mesh structure is incompatible.");
  }
  const primitive = document.meshes?.[0]?.primitives?.[0];
  if (![1, 2, 3].includes(profileSchemaVersion)) {
    throw new Error("geometry.glb profile schema dispatch is incompatible.");
  }
  const expectedAttributes = profileSchemaVersion === 3
    ? [
      "NORMAL", "POSITION", "TEXCOORD_0", "_FACE_INDEX", "_LEAF_INDEX", "_PATCH_INDEX",
    ]
    : ["NORMAL", "POSITION", "_FACE_INDEX", "_LEAF_INDEX", "_PATCH_INDEX"];
  if (primitive?.mode !== 4 || primitive.indices !== undefined
      || Object.keys(primitive.attributes ?? {}).sort().join(",")
        !== expectedAttributes.slice().sort().join(",")) {
    throw new Error("geometry.glb primitive contract is incompatible.");
  }
  const material = document.materials?.[primitive.material];
  if (!material || material.doubleSided !== true
      || ![undefined, "OPAQUE"].includes(material.alphaMode)) {
    throw new Error("geometry.glb material contract is incompatible.");
  }
  const vertexCount = expectedFaceCount * 3;
  const attributes = {};
  for (const name of expectedAttributes) {
    const accessor = document.accessors[primitive.attributes[name]];
    const bufferView = document.bufferViews[accessor.bufferView];
    const components = name === "TEXCOORD_0" ? 2
      : ["NORMAL", "POSITION"].includes(name) ? 3 : 1;
    const expectedType = components === 3 ? "VEC3" : components === 2 ? "VEC2" : "SCALAR";
    const expectedComponent = components > 1 ? 5126 : 5125;
    if (accessor.count !== vertexCount || accessor.componentType !== expectedComponent
        || accessor.type !== expectedType || accessor.normalized === true
        || (accessor.byteOffset ?? 0) !== 0
        || bufferView.buffer !== 0 || bufferView.byteStride !== undefined
        || bufferView.byteLength !== vertexCount * components * 4) {
      throw new Error(`geometry.glb ${name} accessor is incompatible.`);
    }
    const offset = binaryOffset + (bufferView.byteOffset ?? 0)
      + (accessor.byteOffset ?? 0);
    const Constructor = components > 1 ? Float32Array : Uint32Array;
    attributes[name] = new Constructor(bytes, offset, vertexCount * components);
    if (name === "TEXCOORD_0") {
      if (JSON.stringify(accessor.min) !== "[0,0]"
          || JSON.stringify(accessor.max) !== "[1,1]"
          || attributes[name].some((value) => !Number.isFinite(value)
            || value < 0 || value > 1)) {
        throw new Error("geometry.glb TEXCOORD_0 bounds or values are incompatible.");
      }
    }
  }
  return { attributes, document, triangleCount: expectedFaceCount };
}

export async function verifyHash(bytes, expected, label) {
  assertSha256(expected, label);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const actual = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  if (actual !== expected) {
    throw new Error(`${label} failed SHA-256 validation.`);
  }
  return actual;
}

export async function fetchArtifact(base, record) {
  const bytes = await fetchBytes(new URL(record.filename, base));
  if (bytes.byteLength !== record.byte_size) {
    throw new Error(`${record.filename} byte length is invalid.`);
  }
  await verifyHash(bytes, record.sha256, record.filename);
  return bytes;
}

export async function fetchBytes(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load ${url}: HTTP ${response.status}.`);
  }
  return response.arrayBuffer();
}

export function decodeJson(bytes, label) {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch (error) {
    throw new Error(`${label} is not valid UTF-8 JSON.`, { cause: error });
  }
}

export function resolveSceneSamplingProfile(scene) {
  if (scene?.schema_id !== SCENE_SCHEMA || ![1, 2, 3, 4].includes(scene.schema_version)) {
    throw new Error("Scene schema is incompatible.");
  }
  return resolveVersionedSamplingProfile(
    scene.schema_version, scene.profile?.sampling_profile_id, "Scene",
  );
}

export function resolveProfileSamplingProfile(profile) {
  if (profile?.schema_id !== PROFILE_SCHEMA || ![1, 2, 3].includes(profile.schema_version)) {
    throw new Error("Profile schema is incompatible.");
  }
  return resolveVersionedSamplingProfile(
    profile.schema_version, profile.sampling_profile_id, "Profile",
  );
}

export function validateViewerGeneration(scene, profile) {
  const version = scene.viewer_resource_version;
  const profileVersion = profile.schema_version;
  if (!PROFILE_SCHEMA_VERSIONS_BY_RESOURCE[version]?.includes(profileVersion)) {
    throw new Error("Scene resource and profile schema versions are incompatible.");
  }
}

function resolveVersionedSamplingProfile(schemaVersion, declaredProfile, label) {
  if (schemaVersion === 1) {
    if (declaredProfile !== undefined && declaredProfile !== LEGACY_SAMPLING_PROFILE) {
      throw new Error(`${label} historical sampling profile is incompatible.`);
    }
    return LEGACY_SAMPLING_PROFILE;
  }
  if (!SAMPLING_PROFILES.includes(declaredProfile)) {
    throw new Error(`${label} sampling profile is incompatible.`);
  }
  return declaredProfile;
}

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object.`);
  }
}

export function assertSha256(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} SHA-256 is incompatible.`);
  }
}

function assertBounds(value, label, axes) {
  const minimum = value?.[`minimum_${axes}`];
  const maximum = value?.[`maximum_${axes}`];
  if (!Array.isArray(minimum) || !Array.isArray(maximum)
      || minimum.length !== axes.length || maximum.length !== axes.length
      || minimum.some((item, index) => !Number.isFinite(item)
        || !Number.isFinite(maximum[index]) || item > maximum[index])) {
    throw new Error(`${label} are incompatible.`);
  }
}

function assertNearVector(actual, expected, label) {
  if (!Array.isArray(actual) || actual.length !== expected.length
      || actual.some((value, index) => !Number.isFinite(value)
        || Math.abs(value - expected[index]) > 1e-6)) {
    throw new Error(`${label} does not match the validated display data.`);
  }
}
