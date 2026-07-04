// @ts-check

const UNAVAILABLE = "Unavailable";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value, digits = 2) {
  const number = finiteNumber(value);
  return number === null ? UNAVAILABLE : number.toFixed(digits);
}

function formatCount(value, singular, plural) {
  const number = finiteNumber(value);
  if (number === null) {
    return UNAVAILABLE;
  }
  const rounded = Math.round(number);
  return `${rounded} ${rounded === 1 ? singular : plural}`;
}

function formatMetric(value, unit = "", digits = 2) {
  const formatted = formatNumber(value, digits);
  return formatted === UNAVAILABLE ? UNAVAILABLE : `${formatted}${unit ? ` ${unit}` : ""}`;
}

function formatRawFluxStat(summary, key, digits = 1) {
  const formatted = formatMetric(summary?.[key], "umol/m²/s", digits);
  const percent = finiteNumber(summary?.[`${key}_percent_of_target`]);
  return percent === null ? formatted : `${formatted} (${percent.toFixed(0)}% target)`;
}

function formatPercent(value, digits = 1) {
  const number = finiteNumber(value);
  return number === null ? UNAVAILABLE : `${(number * 100).toFixed(digits)}%`;
}

function formatTarget(value, tolerance) {
  const target = formatNumber(value, 0);
  const tol = formatNumber(tolerance, 0);
  return target === UNAVAILABLE || tol === UNAVAILABLE
    ? UNAVAILABLE
    : `${target} umol/m2/s +/- ${tol}`;
}

function labelStatus(value) {
  if (typeof value !== "string" || !value) {
    return UNAVAILABLE;
  }
  return value.replaceAll("_", " ");
}

function bandAbsorbedFlux(bandTotals, bandId) {
  const band = asObject(bandTotals?.[bandId]);
  return band?.absorbed_photon_flux_umol_s;
}

const RAW_FLUX_BUCKETS = [
  { label: "0-20%", minRatio: 0, maxRatio: 0.20 },
  { label: "20-40%", minRatio: 0.20, maxRatio: 0.40 },
  { label: "40-55%", minRatio: 0.40, maxRatio: 0.55 },
  { label: "55-80%", minRatio: 0.55, maxRatio: 0.80 },
  { label: "80-100%", minRatio: 0.80, maxRatio: 1.00 },
  { label: "100-120%", minRatio: 1.00, maxRatio: 1.20 },
  { label: "120-150%", minRatio: 1.20, maxRatio: 1.50 },
  { label: "150%+", minRatio: 1.50, maxRatio: null },
];

function rawGranularityLabel(value) {
  if (value === "leaf_average") {
    return "leaf average";
  }
  if (value === "quadrature_mapped") {
    return "quadrature mapped";
  }
  if (value === "mesh_patch") {
    return "mesh patch surface detail";
  }
  return labelStatus(value);
}

function coverageSourceLabel(source) {
  if (source === "interpolated_runtime_ppfd_map") {
    return "baseline PPFD map sampled at leaf XY positions";
  }
  return labelStatus(source);
}

function rawBucketCountsFromLeafValues(surface) {
  const values = surface?.visualization?.leaf_values;
  const target = finiteNumber(surface?.target_ppfd_umol_m2_s);
  if (!Array.isArray(values) || target === null || target <= 0) {
    return [];
  }
  const buckets = RAW_FLUX_BUCKETS.map((bucket) => ({ ...bucket, leaf_count: 0 }));
  for (const row of values) {
    const raw = finiteNumber(row?.incident_photon_flux_density_umol_m2_s);
    if (raw === null) {
      continue;
    }
    const ratio = raw / target;
    const match = buckets.find((bucket) => (
      bucket.maxRatio === null ? ratio >= bucket.minRatio : ratio < bucket.maxRatio
    ));
    (match || buckets[0]).leaf_count += 1;
  }
  return buckets;
}

function rawBucketCounts(absorption) {
  const explicit = absorption?.raw_leaf_surface_flux_bucket_counts
    || absorption?.raw_leaf_surface_flux_summary?.bucket_counts;
  if (Array.isArray(explicit) && explicit.length > 0) {
    return explicit;
  }
  return rawBucketCountsFromLeafValues(absorption);
}

function rawSurfaceDetailBucketCounts(absorption) {
  const explicit = absorption?.raw_leaf_surface_flux_surface_detail_bucket_counts
    || absorption?.raw_leaf_surface_flux_summary?.surface_detail_bucket_counts;
  return Array.isArray(explicit) ? explicit : [];
}

function spectralModeActive(...payloads) {
  return payloads.some((payload) => {
    const data = asObject(payload);
    const mode = String(data?.fspm_spectral_transport_mode || "");
    const status = String(data?.status || "");
    return mode.startsWith("banded")
      || finiteNumber(data?.banded_transport_band_count) !== null
      || finiteNumber(data?.banded_transport_active_trace_count) !== null
      || (status === "computed" && (
        Array.isArray(data?.band_summaries)
          || Array.isArray(data?.banded_transport_bands)
          || asObject(data?.band_totals) !== null
      ));
  });
}

function hasAnySection(panel) {
  return Boolean(
    asObject(panel?.incident_leaf_surface_flux)
      || asObject(panel?.plant_surface_absorption)
      || asObject(panel?.modeled_spectral_absorption)
      || asObject(panel?.spectral_exposure)
      || asObject(panel?.photosynthetic_light_response_potential)
      || asObject(panel?.photoreceptor_exposure)
      || asObject(panel?.legacy_morphology_response_scaffold),
  );
}

function scenePlantCounts(scene) {
  const plantsPayload = asObject(scene?.plants);
  const plants = Array.isArray(plantsPayload?.plants) ? plantsPayload.plants : [];
  let leafCount = 0;
  for (const plant of plants) {
    const leaves = Array.isArray(plant?.leaves) ? plant.leaves : [];
    leafCount += leaves.length;
  }
  return {
    plant_count: plants.length || null,
    leaf_count: leafCount || null,
  };
}

function fallbackPanelFromScene(scene) {
  const plantsPayload = asObject(scene?.plants);
  if (!plantsPayload) {
    return null;
  }
  const surface = asObject(plantsPayload.surface_flux);
  const counts = {
    ...scenePlantCounts(scene),
    surface_count: surface?.surface_count,
    one_sided_leaf_area_m2: surface?.one_sided_leaf_area_m2,
  };
  const panel = {
    status: "available",
    counts,
    limitations_note: "Lighting-analysis input only; response potentials are unvalidated and are not biological production forecasts.",
  };
  if (surface) {
    panel.incident_leaf_surface_flux = surface;
    panel.plant_surface_absorption = surface;
  }
  return panel;
}

export function fspmPanelData(scene) {
  const panel = asObject(scene?.fspm_metrics) || fallbackPanelFromScene(scene);
  if (!panel) {
    return null;
  }
  const counts = asObject(panel.counts);
  return hasAnySection(panel) || finiteNumber(counts?.plant_count) !== null || finiteNumber(counts?.leaf_count) !== null
    ? panel
    : null;
}

export function hasFspmPanelData(scene) {
  return fspmPanelData(scene) !== null;
}

export function buildFspmPanelSections(scene) {
  const panel = fspmPanelData(scene);
  if (!panel) {
    return [];
  }

  const sections = [];
  const counts = asObject(panel.counts) || {};
  sections.push({
    title: "Plant-model overview",
    rows: [
      ["Plants", formatCount(counts.plant_count, "plant", "plants")],
      ["Leaves", formatCount(counts.leaf_count, "leaf", "leaves")],
      [
        "Receiver samples",
        formatCount(counts.receiver_sample_count ?? counts.surface_count, "sample", "samples"),
      ],
      ["Receiver granularity", labelStatus(counts.receiver_granularity)],
      ["Receiver side policy", labelStatus(counts.receiver_side_policy)],
      ["Mesh surface rows", formatCount(counts.surface_count, "surface", "surfaces")],
      ["One-sided leaf area", formatMetric(counts.one_sided_leaf_area_m2, "m2", 4)],
    ],
  });

  const absorption = asObject(panel.incident_leaf_surface_flux) || asObject(panel.plant_surface_absorption);
  if (absorption) {
    const rawSummary = asObject(absorption.raw_leaf_surface_flux_summary);
    if (rawSummary) {
      const bucketRows = rawBucketCounts(absorption).map((bucket) => [
        `Leaf avg ${bucket.label || `${formatNumber(bucket.min_percent, 0)}%`}`,
        formatCount(bucket.leaf_count, "leaf", "leaves"),
      ]);
      const surfaceDetailRows = rawSurfaceDetailBucketCounts(absorption).map((bucket) => [
        `Surface detail ${bucket.label || `${formatNumber(bucket.min_percent, 0)}%`}`,
        formatCount(bucket.sample_count, "sample", "samples"),
      ]);
      sections.push({
        title: "Raw leaf-surface flux",
        note: "Actual receiver-based incident PPFD at leaf surfaces.",
        rows: [
          [
            "Summary granularity",
            rawGranularityLabel(
              rawSummary.summary_granularity || absorption.raw_leaf_summary_granularity || "leaf_average",
            ),
          ],
          [
            "Visualization granularity",
            rawGranularityLabel(
              rawSummary.visualization_granularity ||
                absorption.raw_visualization_granularity ||
                "leaf_average",
            ),
          ],
          ["Mean", formatRawFluxStat(rawSummary, "mean", 1)],
          ["Min", formatRawFluxStat(rawSummary, "min", 1)],
          ["p05", formatRawFluxStat(rawSummary, "p05", 1)],
          ["Median", formatRawFluxStat(rawSummary, "median", 1)],
          ["p95", formatRawFluxStat(rawSummary, "p95", 1)],
          ["Max", formatRawFluxStat(rawSummary, "max", 1)],
          [
            "Raw incident flux",
            formatMetric(absorption.total_incident_photon_flux_umol_s, "umol/s", 2),
          ],
          ...bucketRows,
          ...surfaceDetailRows,
        ],
      });
    }
    sections.push({
      title: "Plant-location target coverage",
      note: "Coverage uses plant-location/canopy-reference PPFD, not raw leaf-surface receiver values.",
      rows: [
        ["Status", labelStatus(absorption.status)],
        ["Method", labelStatus(absorption.method)],
        [
          "Target PPFD",
          formatTarget(absorption.target_ppfd_umol_m2_s, absorption.target_tolerance_umol_m2_s),
        ],
        [
          "Coverage basis",
          labelStatus(
            absorption.target_classification_basis_label ||
              absorption.target_classification_basis ||
              absorption.target_basis_label ||
              absorption.target_basis,
          ),
        ],
        [
          "Source",
          coverageSourceLabel(absorption.target_classification_source),
        ],
        ["Target-range leaves", formatCount(absorption.target_range_leaf_count, "leaf", "leaves")],
        ["Under-lit leaves", formatCount(absorption.under_lit_leaf_count, "leaf", "leaves")],
        ["Over-lit leaves", formatCount(absorption.over_lit_leaf_count, "leaf", "leaves")],
        [
          "Mean plant-location reference PPFD",
          formatMetric(absorption.target_classification_mean_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Target-capped plant-location flux",
          formatMetric(
            absorption.target_capped_incident_flux_total_umol_s ??
              absorption.target_capped_flux_total_umol_s,
            "umol/s",
            2,
          ),
        ],
        [
          "Excess above target",
          formatMetric(
            absorption.excess_incident_flux_above_target_umol_s ??
              absorption.excess_flux_above_target_umol_s,
            "umol/s",
            2,
          ),
        ],
        [
          "Deficit to target",
          formatMetric(
            absorption.deficit_to_target_incident_flux_umol_s ??
              absorption.under_target_deficit_umol_s,
            "umol/s",
            2,
          ),
        ],
        [
          "Plant-to-plant target-capped coverage CV",
          formatPercent(
            absorption.plant_to_plant_target_capped_incident_flux_cv ??
              absorption.plant_to_plant_target_capped_flux_cv,
            1,
          ),
        ],
        [
          "Lower-tail raw flux density",
          formatMetric(absorption.lower_tail_raw_flux_density_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Lower-tail plant-location reference PPFD",
          formatMetric(
            absorption.lower_tail_target_classification_ppfd_umol_m2_s,
            "umol/m2/s",
            1,
          ),
        ],
        [
          "Target-capped plant-location mean PPFD",
          formatMetric(
            absorption.target_capped_incident_mean_flux_density_umol_m2_s ??
              absorption.target_capped_mean_flux_density_umol_m2_s,
            "umol/m2/s",
            1,
          ),
        ],
      ],
    });
  }

  const spectralAbsorption = asObject(panel.modeled_spectral_absorption);
  const spectral = asObject(panel.spectral_exposure);
  const response = asObject(panel.photosynthetic_light_response_potential);
  const exposure = asObject(panel.photoreceptor_exposure);
  const spectralActive = spectralModeActive(spectralAbsorption, spectral, response, exposure);
  if (spectralAbsorption && spectralActive) {
    sections.push({
      title: "modeled spectral leaf absorption",
      rows: [
        ["Status", labelStatus(spectralAbsorption.status)],
        ["Method", labelStatus(spectralAbsorption.method)],
        ["Optical profile", labelStatus(spectralAbsorption.optical_profile_id)],
        ["Source spectrum basis", labelStatus(spectralAbsorption.source_spectral_basis)],
        ["Scalar flux basis", labelStatus(spectralAbsorption.scalar_flux_basis)],
        [
          "Target-capped absorbed PAR PPFD",
          formatMetric(
            spectralAbsorption.target_capped_absorbed_par_ppfd ??
              spectralAbsorption.target_capped_absorbed_par_ppfd_umol_m2_s,
            "umol/m2/s",
            1,
          ),
        ],
        [
          "Target-capped absorbed ePAR PPFD",
          formatMetric(
            spectralAbsorption.target_capped_absorbed_epar_ppfd ??
              spectralAbsorption.target_capped_absorbed_epar_ppfd_umol_m2_s,
            "umol/m2/s",
            1,
          ),
        ],
        [
          "Target-capped PAR fraction of raw",
          formatPercent(spectralAbsorption.target_capped_absorbed_par_fraction_of_raw, 1),
        ],
        [
          "Over-target absorbed PAR",
          formatMetric(spectralAbsorption.excess_absorbed_par_ppfd_above_target_cap, "umol/m2/s", 1),
        ],
        [
          "Under-target leaf fraction",
          formatPercent(spectralAbsorption.under_target_leaf_fraction, 1),
        ],
        [
          "In-target leaf fraction",
          formatPercent(spectralAbsorption.in_target_leaf_fraction, 1),
        ],
        [
          "Over-target leaf fraction",
          formatPercent(spectralAbsorption.over_target_leaf_fraction, 1),
        ],
        [
          "Incident PAR PPFD",
          formatMetric(spectralAbsorption.scalar_incident_par_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Raw modeled absorbed PAR PPFD",
          formatMetric(spectralAbsorption.absorbed_par_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Raw modeled absorbed ePAR PPFD",
          formatMetric(spectralAbsorption.absorbed_epar_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        ["Blue", formatMetric(spectralAbsorption.absorbed_blue_ppfd_umol_m2_s, "umol/m2/s", 1)],
        ["Green", formatMetric(spectralAbsorption.absorbed_green_ppfd_umol_m2_s, "umol/m2/s", 1)],
        ["Orange", formatMetric(spectralAbsorption.absorbed_orange_ppfd_umol_m2_s, "umol/m2/s", 1)],
        ["Red", formatMetric(spectralAbsorption.absorbed_red_ppfd_umol_m2_s, "umol/m2/s", 1)],
        ["Far-red", formatMetric(spectralAbsorption.absorbed_far_red_ppfd_umol_m2_s, "umol/m2/s", 1)],
        ["Absorbed fraction", formatPercent(spectralAbsorption.absorbed_fraction, 1)],
        ["Reflected fraction", formatPercent(spectralAbsorption.reflected_fraction, 1)],
        ["Transmitted fraction", formatPercent(spectralAbsorption.transmitted_fraction, 1)],
      ],
    });
  }

  if (spectral && spectralActive) {
    const bands = asObject(spectral.band_totals);
    sections.push({
      title: "spectral exposure",
      rows: [
        ["Status", labelStatus(spectral.status)],
        ["Method", labelStatus(spectral.method)],
        ["Absorbed PAR", formatMetric(spectral.total_absorbed_par_photon_flux_umol_s, "umol/s", 2)],
        ["Blue absorbed flux", formatMetric(bandAbsorbedFlux(bands, "blue"), "umol/s", 2)],
        ["Green absorbed flux", formatMetric(bandAbsorbedFlux(bands, "green"), "umol/s", 2)],
        ["Red absorbed flux", formatMetric(bandAbsorbedFlux(bands, "red"), "umol/s", 2)],
        ["Far-red absorbed flux", formatMetric(bandAbsorbedFlux(bands, "far_red"), "umol/s", 2)],
      ],
    });
  }

  if (response && spectralActive) {
    sections.push({
      title: "photosynthetic light-response potential",
      rows: [
        ["Calibration", labelStatus(response.calibration_status)],
        ["Input basis", labelStatus(response.input_basis)],
        ["Area-weighted local response", formatPercent(response.area_weighted_mean_local_response_0_1, 1)],
        ["Equal-plant normalized response", formatPercent(response.equal_plant_mean_normalized_response_0_1, 1)],
        ["P10 local response", formatPercent(response.local_response_p10_0_1, 1)],
        ["Bottom-decile response", formatPercent(response.bottom_decile_area_weighted_response_0_1, 1)],
        ["Nonuniformity response retention", formatPercent(response.nonuniformity_response_retention_0_1, 1)],
        ["Plant-to-plant response CV", formatPercent(response.plant_to_plant_photosynthetic_response_cv, 1)],
      ],
    });
  }

  if (exposure && spectralActive) {
    const pss = asObject(exposure.phytochrome_pss_proxy);
    const dose = asObject(exposure.blue_photon_dose);
    sections.push({
      title: "photoreceptor exposure",
      rows: [
        ["Status", labelStatus(exposure.status)],
        ["Method", labelStatus(exposure.method)],
        ["Blue PFD", formatMetric(exposure.mean_absorbed_blue_pfd_umol_m2_s, "umol/m2/s", 1)],
        ["Green PFD", formatMetric(exposure.mean_absorbed_green_pfd_umol_m2_s, "umol/m2/s", 1)],
        ["Red PFD", formatMetric(exposure.mean_absorbed_red_pfd_umol_m2_s, "umol/m2/s", 1)],
        ["Far-red PFD", formatMetric(exposure.mean_absorbed_far_red_pfd_umol_m2_s, "umol/m2/s", 1)],
        ["Blue fraction of PAR", formatPercent(exposure.mean_absorbed_blue_fraction_of_par, 1)],
        [
          "R:FR diagnostic",
          formatMetric(exposure.mean_absorbed_red_to_far_red_ratio_diagnostic, "", 2),
        ],
        ["PSS proxy", pss?.value === null || pss?.value === undefined ? labelStatus(pss?.status) : formatNumber(pss.value, 3)],
        [
          "Blue dose",
          dose?.value_umol_m2 === null || dose?.value_umol_m2 === undefined
            ? labelStatus(dose?.status)
            : formatMetric(dose.value_umol_m2, "umol/m2", 1),
        ],
      ],
    });
  }

  const scaffold = asObject(panel.legacy_morphology_response_scaffold);
  if (scaffold) {
    sections.push({
      title: "Legacy morphology-response scaffold",
      rows: [
        ["Status", labelStatus(scaffold.status)],
        ["Method", labelStatus(scaffold.method)],
        ["Hypothesis status", labelStatus(scaffold.morphology_hypothesis_status)],
      ],
    });
  }

  sections.push({
    title: "Limitations",
    note: typeof panel.limitations_note === "string" && panel.limitations_note
      ? panel.limitations_note
      : "Lighting-analysis input only; response potentials are unvalidated and are not biological production forecasts.",
  });
  return sections;
}
