# Phase 23B Conventional Rex absorbed-photon policy

Phase 23B Part 3 is pure optical post-processing. It accepts only a completed,
successful Conventional Rex native incident workspace and does not execute
Radiance or modify native incident artifacts.

## Patch equations and physical area

For physical patch `p` and spectral band `b`, front and back receivers describe
the two incident hemispheres of the same infinitely thin leaf patch:

```text
Eincident,p,b    = Efront,p,b + Eback,p,b
Eabsorbed,p,b    = Ab × Eincident,p,b
Etransmitted,p,b = Tb × Eincident,p,b
Ereflected,p,b   = Rb × Eincident,p,b
Qabsorbed,p,b    = Eabsorbed,p,b × patch_area_p
```

The physical patch area is used once after combining the front and back fields.
It is not doubled. Front and back incident, absorbed, transmitted, and reflected
contributions remain available independently. Every local interaction must
close as absorbed + transmitted + reflected ≈ incident.

Transmitted and reflected partitions describe the immediate local interaction.
They are not net escaped-light totals and do not account for whether those
photons later encounter another surface.

## Spectral authority and aggregation

All A/T/R values come from the persisted Conventional-source-weighted Rex
payload and committed material plan. Blue, green, orange, and red are summed to
form the primary absorbed PAR result. Far-red stays separate.

Scalar PAR uses the independently weighted scalar absorptance and scalar native
receiver array. It is a validation diagnostic only. Its absorbed whole-plant
flux, area-weighted PFD, front/back contributions, and receiver/patch RMSE are
compared with the four-band result without scaling or a pass threshold. Scalar
PAR is never added to the spectral bands.

Patch results aggregate deterministically to each leaf and to the whole plant.
Reported quantities include incident, absorbed, transmitted, and reflected
photon flux; area-weighted incident and absorbed PFD; front/back absorbed
contributions; physical area; and local closure error.

## Workspace and artifacts

Before calculation, the loader reconstructs the typed native plan and validates
the source, optics, material, plant, receiver, bundle, scene, execution, command,
DAT, and artifact identities and hashes. It requires six finite nonnegative
receiver arrays of shape `(1024,)`, 512 stable front/back patch pairs, and no
failure summary. Partial, stale, substituted, or SMD-identified workspaces are
rejected before output is written.

Outputs are isolated from native results:

```text
conventional_rex_transport/absorbed/
  absorbed_photon_summary.json
  absorbed_patch_metrics.npz
```

The deterministic NPZ contains stable patch/leaf/receiver indices, physical
areas, five-band coefficients and partitions, explicit four-band PAR arrays,
far-red arrays, and scalar validation arrays. The summary records all scientific
identities, input and output hashes, coefficients, patch/leaf/plant aggregates,
the scalar diagnostic, and maximum local closure errors. Existing matching
outputs are accepted idempotently; incomplete or inconsistent outputs are not
overwritten.

The claim boundary is optical-only. Photosynthesis, DLI, biomass, growth, yield,
electrical-efficiency comparisons, optimization, visualization, and system
comparison reports are outside this phase.
