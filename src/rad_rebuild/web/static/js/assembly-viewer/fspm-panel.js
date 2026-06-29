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
      ["Mesh surface rows", formatCount(counts.surface_count, "surface", "surfaces")],
      ["One-sided leaf area", formatMetric(counts.one_sided_leaf_area_m2, "m2", 4)],
    ],
  });

  const absorption = asObject(panel.incident_leaf_surface_flux) || asObject(panel.plant_surface_absorption);
  if (absorption) {
    sections.push({
      title: "incident leaf-surface PPFD",
      rows: [
        ["Status", labelStatus(absorption.status)],
        ["Method", labelStatus(absorption.method)],
        [
          "Target PPFD",
          formatTarget(absorption.target_ppfd_umol_m2_s, absorption.target_tolerance_umol_m2_s),
        ],
        [
          "Target classification basis",
          labelStatus(
            absorption.target_classification_basis_label ||
              absorption.target_classification_basis ||
              absorption.target_basis_label ||
              absorption.target_basis,
          ),
        ],
        [
          "Target classification source",
          labelStatus(absorption.target_classification_source),
        ],
        ["Target-range leaves", formatCount(absorption.target_range_leaf_count, "leaf", "leaves")],
        ["Under-lit leaves", formatCount(absorption.under_lit_leaf_count, "leaf", "leaves")],
        ["Over-lit leaves", formatCount(absorption.over_lit_leaf_count, "leaf", "leaves")],
        [
          "Target-classification mean PPFD",
          formatMetric(absorption.target_classification_mean_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Target-capped incident flux",
          formatMetric(
            absorption.target_capped_incident_flux_total_umol_s ??
              absorption.target_capped_flux_total_umol_s,
            "umol/s",
            2,
          ),
        ],
        [
          "Excess incident above target",
          formatMetric(
            absorption.excess_incident_flux_above_target_umol_s ??
              absorption.excess_flux_above_target_umol_s,
            "umol/s",
            2,
          ),
        ],
        [
          "Deficit to target incident flux",
          formatMetric(
            absorption.deficit_to_target_incident_flux_umol_s ??
              absorption.under_target_deficit_umol_s,
            "umol/s",
            2,
          ),
        ],
        [
          "Plant-to-plant target-capped incident CV",
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
          "Lower-tail target-classification PPFD",
          formatMetric(
            absorption.lower_tail_target_classification_ppfd_umol_m2_s,
            "umol/m2/s",
            1,
          ),
        ],
        [
          "Target-capped incident mean density",
          formatMetric(
            absorption.target_capped_incident_mean_flux_density_umol_m2_s ??
              absorption.target_capped_mean_flux_density_umol_m2_s,
            "umol/m2/s",
            1,
          ),
        ],
        [
          "Raw incident flux",
          formatMetric(absorption.total_incident_photon_flux_umol_s, "umol/s", 2),
        ],
        [
          "Legacy broadband absorbed flux",
          formatMetric(absorption.total_absorbed_photon_flux_umol_s, "umol/s", 2),
        ],
        [
          "Legacy broadband absorbed fraction",
          formatPercent(absorption.mean_absorbed_fraction_of_incident, 1),
        ],
        [
          "Legacy broadband absorbed-flux CV",
          formatPercent(absorption.plant_to_plant_absorbed_photon_flux_cv, 1),
        ],
      ],
    });
  }

  const spectralAbsorption = asObject(panel.modeled_spectral_absorption);
  if (spectralAbsorption) {
    sections.push({
      title: "modeled spectral leaf absorption",
      rows: [
        ["Status", labelStatus(spectralAbsorption.status)],
        ["Method", labelStatus(spectralAbsorption.method)],
        ["Optical profile", labelStatus(spectralAbsorption.optical_profile_id)],
        ["Source spectrum basis", labelStatus(spectralAbsorption.source_spectral_basis)],
        ["Scalar flux basis", labelStatus(spectralAbsorption.scalar_flux_basis)],
        [
          "Incident PAR PPFD",
          formatMetric(spectralAbsorption.scalar_incident_par_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Modeled absorbed PAR PPFD",
          formatMetric(spectralAbsorption.absorbed_par_ppfd_umol_m2_s, "umol/m2/s", 1),
        ],
        [
          "Modeled absorbed ePAR PPFD",
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

  const spectral = asObject(panel.spectral_exposure);
  if (spectral) {
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

  const response = asObject(panel.photosynthetic_light_response_potential);
  if (response) {
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

  const exposure = asObject(panel.photoreceptor_exposure);
  if (exposure) {
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
