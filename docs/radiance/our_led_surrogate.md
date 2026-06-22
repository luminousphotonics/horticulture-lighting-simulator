# Our LED System Surrogate

The active `Our LED System` path is the internal `SMD` mode. It is a module-level physical surrogate, not a measured finished-luminaire IES.

The surrogate combines:

- a fixed `145`-site LED package mix per module
- an explicit optical stack with PMMA lid and PTFE cavity treatment
- a curve-driven package electrical and efficacy model
- digitized emitter SPDs for warm-white, cool-white, and deep-red package spectral efficacy bookkeeping
- photon-first normalization and basis generation aligned with PPFD transport

## Module Definition

- Module profile tag: `145led_clear_lid_ptfe_stack_v2`
- Nominal PCB envelope per module: `150 mm x 150 mm`
- Nominal emitting window / macro patch: `120 mm x 120 mm`
- LED site pitch: `7 mm`
- Real package-count mix per module:
  - `52` warm-white
  - `52` cool-white
  - `41` deep-red

This is a repeated module surrogate used by the layout engine. It is not described as a finished fixture photometric measurement.

## Optical Stack

- Default optics mode: physical stack surrogate (`OPTICS=stack`)
- PMMA lid envelope: `128 mm x 128 mm x 3 mm`
- PMMA index of refraction: `1.49`
- PMMA transmission target: about `0.92` at `3 mm`
- Optical stack height above emitter plane: `8 mm`
- PTFE cavity liner reflectance: `0.97`
- PTFE liner thickness: `0.508 mm`
- Gasket aperture: `126 mm`
- Bezel pocket: `129 mm`

The PMMA lid is modeled explicitly as a dielectric in the active optical stack path. The PTFE cavity is represented as a high-reflectance surrounding structure, but the surrogate does not claim a full measured luminaire optical reconstruction.

## Electrical And Photon Baseline

Near the intended operating point, the active package assumptions are:

- nominal package electrical power:
  - warm-white: `0.68 W`
  - cool-white: `0.68 W`
  - deep-red: `0.44 W`
- nominal package PPE:
  - warm-white: `2.73 µmol/J`
  - cool-white: `2.81 µmol/J`
  - deep-red: `4.13 µmol/J`

At the current default curve-model nominal operating point, the validation script reports approximately:

- white nominal current: `234.29 mA` per package
- red nominal current: `233.66 mA` per package
- nominal module input power: `93.39 W`
- nominal LED supply power: `88.76 W`
- nominal source photons before PMMA: `266.34 µmol/s`
- nominal emitted photons after PMMA: `245.04 µmol/s`
- nominal source PPE: `2.8519 µmol/J`
- nominal wall-plug PPE: `2.6237 µmol/J`
- nominal thermal multiplier: `0.985`

These values are module-level surrogate outputs from the active curve model, not measured finished-fixture certification values.

## Runtime Modeling Philosophy

The active SMD path is curve-driven and photon-first.

- The engine evaluates module behavior from package-level forward-voltage curves, relative-PPE curves, and digitized package SPDs.
- The runtime solves current and voltage for the active white and red channels at the requested per-module electrical state.
- The SPDs are used upstream inside the package/channel photon model to carry wavelength-aware PAR photon efficacy metadata for the warm-white, cool-white, and deep-red package groups.
- The live photon authority remains package/channel `source_photon_umol_s`; the runtime does not introduce any lumen/candela bridge into proposed-system PPFD normalization.
- Package electrical power is converted to photons first.
- Thermal, driver, wiring, and PMMA effects are then applied in the runtime module state.
- Radiance transport remains scalar, but the emitted source strength is derived from the module photon model rather than from a downstream watts-to-photons shortcut.

In the active curve path, the relevant reported module quantities are:

- `source_photon_umol_s`: photons before PMMA transmission
- `output_photon_umol_s`: photons after PMMA transmission
- `wall_plug_ppe_umol_per_j`: output photons divided by module input watts

## Thermal And Loss Modeling

The active path no longer uses the older tiered thermal description as its primary model.

Instead, the curve model applies:

- driver efficiency: `0.96`
- wiring efficiency: `0.99`
- PMMA transmission: `0.92`
- a continuous thermal multiplier parameterized by:
  - reference input power: nominal module input watts by default
  - reference multiplier: `0.985`
  - slope: `0.0015` per watt
  - clamp range: `0.90` to `1.00`

This means the active SMD runtime uses a continuous thermal derate with curve-driven package behavior. The older two-tier thermal fallback and the legacy power-droop law remain only as compatibility behavior for non-curve / legacy paths.

## Basis And Solve Space

The SMD precomputed basis is now aligned with module photon output rather than treated as a purely electrical-response basis.

- The runtime package model computes module photon output from the active electrical state first.
- Basis extraction records the optical transport response in that photon space.
- The solver still schedules modules in electrical watts per module for reporting and control compatibility.
- The basis-to-runtime transform converts those scheduled watts through the active curve model using `source_photon_umol_s`.
- This keeps solve, playback, and reuse paths aligned with the current runtime optical and photon model.

In other words, electrical scheduling remains upstream, but the optical transport basis is interpreted through the active module photon model instead of through a stale linear watts-to-photons assumption.

## Layout And Placement

The SMD system is arranged by module-placement logic rather than by finished-fixture envelope packing.

- Mechanical spacing footprint used by the layout engine: `152.4 mm x 165.0 mm`
- Mounting height default: `18 in`
- Supported layout families include:
  - square ring arrays
  - rectangular ring arrays
  - exact tiled horticultural layouts with explicit module groups

Placement is centered within the usable room span after wall inset and module-footprint guard allowances are applied.

## Calibration-Sensitive Elements

The most important calibration knobs in the active surrogate are:

- ring or module power schedule
- module pitch and placement family
- macro patch / emitting window size
- PMMA transmission, thickness, and refractive index
- PTFE cavity reflectance and aperture geometry
- package nominal power and PPE assumptions
- curve files for forward voltage and relative PPE
- digitized emitter SPD files for the warm-white, cool-white, and deep-red package groups
- continuous thermal model parameters
- mounting height

## Validation Intent

The surrogate is intended to preserve:

- broad multi-module overlap without point-source behavior
- realistic center-to-edge shaping from ring-wise or module-wise power allocation
- sensitivity to the explicit PMMA + PTFE optical stack
- electrical-to-photon behavior that changes with the active runtime package model
- consistency between direct rendering, solve, playback, and precomputed-basis reuse

It is not intended to claim a measured finished-luminaire IES reconstruction.
