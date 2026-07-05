# Rex lettuce leaf optical profile v0.2

This folder contains a source-bound Rex lettuce (`Lactuca sativa` cultivar `Rex`) leaf optical profile built from digitized published Figure 7 curves.

## Source

Frontiers in Plant Science 2025, Figure 7, Rex lettuce leaf absorptance, transmittance, and reflectance curves by treatment.

Provenance: `digitized_from_published_figure`  
Validation status: `research_derived_digitized_unvalidated_in_simulator`

## Corrected v0.2 behavior

This v0.2 bundle does **not** clip reflectance/transmittance to the direct absorptance range. The model grid is 404-797 nm because reflectance and transmittance support that range across all treatment curves.

Direct absorptance is used where it exists. Where direct absorptance is missing but reflectance and transmittance are available, model absorptance is computed from:

```text
absorptance_fraction_implied_from_rt_raw = 1 - reflectance_fraction_raw_digitized - transmittance_fraction_raw_digitized
```

If the raw implied value is negative because the digitized R+T budget exceeds 1, the model clamps absorptance to 0 for that wavelength and scales model R/T to preserve a physical A+T+R=1 budget. The raw digitized values are preserved in separate columns for auditability.

The primary absorption column is:

```text
absorptance_fraction_model
```

with basis flags in:

```text
absorptance_basis
```

Basis values:

- `raw_digitized`
- `implied_from_reflectance_transmittance`
- `implied_from_reflectance_transmittance_clamped_zero_budget_excess`
- `mean_of_treatment_models`

## Recommended simulator usage

For modeled absorbed photon flux:

```text
absorbed_photon_flux_nm = incident_photon_flux_nm * absorptance_fraction_model
```

Do not peak-normalize the curves. Values are physical fractions from 0.0 to 1.0.

Use `reflectance_fraction_model` and `transmittance_fraction_model` when you need a physical A+T+R=1 optical budget. Use the `*_raw_digitized` columns only for audit/provenance or later reprocessing.

## Files

- `rex_leaf_optical_properties_digitized_v0_2.csv` — primary long-format model file.
- `rex_leaf_optical_properties_digitized_v0_2_wide.csv` — wide-format inspection file.
- `rex_leaf_optical_bands_from_digitized_curves_v0_2.csv` — unweighted band-level summary for inspection only.
- `rex_leaf_optical_clean_points_v0_2.csv` — cleaned digitized points in long format.
- `rex_leaf_optical_profile_manifest_v0_2.json` — machine-readable metadata.
- `rex_leaf_optical_budget_qc_summary_v0_2.csv` — model-budget QC summary.
- `raw_digitized/` — cleaned property/treatment curves copied as separate CSVs.
- `qc/` — additional QC summaries.

## Limitations

This is a leaf optical profile for converting incident spectral photon flux into modeled absorbed photon flux. It is not a validated photosynthesis, morphology, biomass, or yield model.
