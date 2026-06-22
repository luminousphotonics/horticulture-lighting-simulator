# 1000W HPS Research Comparator

The research workflow now treats `1000W HPS` as a strict-IES horticultural comparator family rather than a single legacy `1000W DE HPS` surrogate.

The current hierarchy is:

- primary HPS research model: `GLH-KARMA-8-HPS1000.IES`
- alternate horticultural IES comparator: `archives/hps_comparator_ies/GLH-OG-8-HPS10001.IES`
- problematic zero-opening reference case: `archives/hps_comparator_ies/GLH-HDE-E-HPS1000.IES`
- legacy handcrafted baseline: the older surrogate path enabled with `HPS_IES_MODE=0`

## Primary Research Model

The primary `1000W HPS` research path now uses:

- manufacturer IES photometry from `ies_sources/GLH-KARMA-8-HPS1000.IES`
- a digitized relative SPD from `curve_data/hps/DEhps_1000w_spd.csv`
- an explicit fixture-level photon-output anchor exposed through `HPS_FIXTURE_PPF`

This follows the same photon-first philosophy used elsewhere in the engine:

- IES: angular distribution authority
- SPD: wavelength-aware photon conversion authority
- fixture PPF anchor: final normalization authority

KARMA was selected as the primary research model because it provides a native usable IES opening, represents a horizontal-lamp horticultural reflector, and gave the best overall strict-IES balance in the comparison work without relying on the legacy handcrafted surrogate.

## Variant Roles

### KARMA

- primary HPS research comparator
- native `ies2rad` source geometry
- horizontal-lamp horticultural reflector
- preferred when the study needs the most defensible strict-IES HPS model in this repo

### OG

- alternate strict-IES horticultural comparator
- native `ies2rad` source geometry
- vertical-lamp SE horticultural reflector
- retained because it materially outperformed HDE and remains useful as a comparison case

### HDE

- documented strict-IES reference for problematic zero-opening photometry
- the IES is still treated as the angular-distribution authority
- because the luminous opening is `0 0 0`, this path requires a manual downward effective aperture rebuild
- retained as a reference case, not the preferred research default

### Legacy Surrogate

- historical handcrafted baseline enabled with `HPS_IES_MODE=0`
- still useful for backward comparison and legacy result continuity
- no longer the primary HPS default for research workflow decisions

## Active Source Handling

The native-opening horticultural IES files are preferred when they provide usable emitting geometry.

- KARMA uses native `ies2rad` geometry with the IES candela table still authoritative
- OG uses native `ies2rad` geometry in the same style
- HDE keeps the documented flat-aperture workaround only because its luminous opening is unusable

The original manufacturer IES files remain unchanged.

## Normalization

All strict-IES HPS variants reuse the same shared HPS SPD and normalization logic.

- `lumens_per_lamp` from the IES header is logged
- integrated luminaire lumens are logged
- the SPD supplies the `umol/lm` bridge
- pre-normalization fixture PPF is computed from luminaire lumens times the SPD bridge
- `HPS_FIXTURE_PPF` remains the final emitted-fixture normalization authority

## Research Default Meaning

In this workflow, `default` now means most defensible research comparator, not highest canopy capture.

- KARMA is the primary strict-IES HPS research model
- OG remains selectable as an alternate strict-IES horticultural comparator
- HDE remains selectable as a documented problematic-photometry reference
- the legacy surrogate remains selectable as a legacy baseline
