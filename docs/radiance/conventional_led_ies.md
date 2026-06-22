# Conventional LED IES Comparator

The active `Conventional LED System` comparator models the EnVision Qube `QB-FSG-8B-7T660W` as a measured-photometry horticultural comparator using:

- manufacturer IES photometry from `ies_sources/qube_660w_8bar.ies`
- a digitized relative SPD from `curve_data/conventional/qube_660w_spd.csv`
- a default fixture-level PAR photon flux anchor of `2240.0 µmol/s` (`800.0 W x 2.8 µmol/J`)

This is intended to align with the engine's existing SMD spectral/photon handling philosophy. It is not presented as an exact manufacturer-true spectroradiometric reconstruction.
The active comparator preserves the same measured luminaire photometric field shape and only modernizes the absolute fixture normalization to a contemporary `800 W / 2.8 µmol/J / 2240 µmol/s` class.

## Fixture Definition

- Model: `QB-FSG-8B-7T660W`
- Fixture type: `8`-bar horticultural LED fixture
- Photon-flux anchor: `2240.0 µmol/s` by default
- Beam angle reference: `120°`
- Mechanical dimensions: `46.85 in x 42.8 in x 4.26 in`
- Input power reference for the modernized comparator anchor: `800.0 W`
- IES header power retained for the measured EnVision photometric file: `663.2 W`

## IES Interpretation

The file `qube_660w_8bar.ies` is interpreted as a whole-fixture goniophotometer measurement of the complete luminaire.

- One active Radiance emitter is instantiated per fixture.
- The IES is not replicated across eight emitting bars.
- The `8`-bar structure is retained only as non-emitting reference geometry for fixture footprint placement, documentation, overlays, and optional housing or occlusion use.
- The active Radiance source is a single downward-facing flat aperture using the IES angular distribution, not a six-face luminous box.

This keeps the angular distribution faithful to the measured whole-fixture photometry instead of multiplying the measured IES into an artificial bar-by-bar emitter array.

## Active Radiance Source Representation

The EnVision comparator rewrites the raw `ies2rad` box-style luminaire into a fixture-level downward aperture.

- `ies2rad` initially produces a six-face luminous box with `boxcorr`.
- The active comparator rewrites that into a single downward emitting aperture using `flatcorr`.
- The retained emitting polygon is the bottom face of the whole-fixture luminaire envelope, with downward normal orientation.
- Top and side luminous faces are removed from the active source representation.
- The retained `8`-bar overlay geometry remains non-emitting and is written only for footprint, layout, overlay, and optional proxy / occlusion use.

This is more defensible for a suspended horticultural fixture because the useful radiative aperture is downward-facing; allowing the whole luminaire body to emit from side and top faces creates artificial lateral and upward spill that can suppress canopy delivery in otherwise open rooms.

## SPD Use In Photon Conversion

The file `qube_660w_spd.csv` is used physically, not cosmetically.

- It is read as a relative spectral power distribution.
- The SPD is integrated with wavelength-dependent photon-energy conversion so the PAR photon efficacy varies correctly with wavelength.
- The resulting wavelength-aware radiant-watt to photon conversion follows the same logic pattern used by the SMD path: relative spectral shape drives photon accounting, while Radiance still uses scalar emitters for transport.
- The SPD is also used to derive the luminous-to-photon bridge needed around the IES path, keeping luminous quantities separate from the final photon normalization.

In other words, the SPD is not only used to tint display color. It controls the radiant-power-to-photon conversion factor that links the IES photometric distribution to PAR photon output.

## Absolute Normalization

The comparator combines the input data in three distinct roles:

- IES: angular distribution of the whole fixture
- SPD: relative spectral shape and wavelength-aware photon conversion
- Default anchor: absolute fixture-level output normalization at `2240.0 µmol/s`

The engine does not infer absolute fixture PPF from lumens alone.

Instead, it:

1. Converts the IES into a scalar Radiance source using the existing IES pipeline, then rewrites the default six-face luminous box into a single downward aperture.
2. Uses the digitized SPD to derive wavelength-aware PAR photon efficacy and the luminous-to-photon bridge.
3. Computes the pre-normalization fixture photon output directly from `IES lumens x SPD-derived umol/lm`, rather than from any fixed legacy `lm/W` radiant shortcut.
4. Scales the resulting whole-fixture emitter so the final emitted photon output matches the default `2240.0 µmol/s` fixture anchor.

Only this absolute normalization changes. The IES angular distribution, SPD-based photon bridge, source geometry, correction function, and layout logic are intentionally left unchanged, so the field shape is preserved apart from uniform scaling.

If luminous quantities are needed internally, they remain intermediate bridge values rather than the final photon authority.

## Geometry And Layout

- Placement and packing use the EnVision fixture footprint rather than the older `6`-bar SPYDR surrogate dimensions.
- The active default dimensions are `46.85 in x 42.8 in`.
- The internal bar overlays are non-emitting reference geometry only.
- `full` and `practical` layout policies remain available, with the same engine-facing interfaces as before.

## Relationship To SMD Philosophy

The comparator follows the same general philosophy as the SMD runtime:

- photon accounting is explicit
- wavelength-dependent conversion is preserved
- emitted output is normalized from physically meaningful anchors
- Radiance transport remains scalar, with spectral effects folded into the photon conversion and normalization steps

This keeps the conventional comparator consistent with the engine's broader photon-first modeling approach without introducing a separate, disconnected shortcut path.

## Retained Legacy Compatibility

The older SR-3h47 public-data surrogate remains in the codebase as a compatibility fallback.

- It is no longer the default conventional comparator.
- It can still be activated explicitly with `SPYDR_IES_MODE=0`.
- The active runtime writes generated conventional layout and power metadata into `runtime_state/`, while the legacy SR-3h47 public surrogate reference CSVs are retained under `archives/conventional_sr3h47_reference/`.

## Remaining Assumptions

- The digitized SPD is treated as a relative spectral shape, not as absolute lab spectroradiometry.
- The IES-to-Radiance conversion still depends on the existing Radiance photometric conversion bridge used by the engine.
- The active whole-fixture source uses a single downward aperture surrogate for transport rather than a full CAD-faithful emitting housing model.
- The fixture orientation remains subject to the existing IES rotation controls.
- The non-emitting `8`-bar reference geometry uses an even-width, even-gap reconstruction when explicit bar cross-section data is not supplied separately.

These assumptions are deliberate simplifications for comparative simulation, not claims of exact manufacturer reconstruction.
