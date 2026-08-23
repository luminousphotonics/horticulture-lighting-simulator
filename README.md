# Horticulture Lighting Simulator

**Radiance-based horticultural lighting simulation with deterministic plant geometry and multispectral light transport.**

The Horticulture Lighting Simulator is research software for comparing horticultural lighting systems at two levels:

1. the plant-free photosynthetic photon flux density (PPFD) field; and
2. the modeled interaction of that light with explicit plant surfaces.

The application supports the **Modularized LED System**, **Conventional LED System**, and **1000W HPS System** through a browser-based interface with PPFD metrics, heatmaps, interactive plots, and 3D plant visualization.

## Purpose

Mean PPFD and reference-plane uniformity describe the lighting field, but they do not show how light is distributed across oriented and self-shading leaves. The Horticulture Lighting Simulator connects these two views while keeping them scientifically distinct.

The baseline analysis evaluates lighting on a plant-free horizontal reference plane. The optional functional-structural plant model (FSPM) analysis then applies the validated source state to deterministic juvenile Rex lettuce geometry and traces light across the modeled leaf surfaces.

## Key features

- Radiance-based light transport.
- Plant-free PPFD mapping and spatial-uniformity metrics.
- Three supported horticultural lighting systems.
- Deterministic natural-fit placement of juvenile Rex lettuce models.
- Multispectral leaf-surface transport across four PAR bands, with optional separate far-red analysis.
- Modeled incident, absorbed, transmitted, and reflected photon accounting.
- Interactive PPFD heatmaps, 3D scatter plots, and plant-layout visualization.
- Precomputed playback for fast public use.
- Trusted-local live simulation for native Radiance execution.
- Versioned artifacts and integrity checks for reproducible results.

## Supported lighting systems

| Lighting system | Operating behavior |
| --- | --- |
| **Modularized LED System** | Target-controlled LED output using a deterministic modular fixture layout and uniform module dimming |
| **Conventional LED System** | Target-controlled output using validated directional and spectral source data |
| **1000W HPS System** | Fixed-output operation using validated directional and spectral source data |

## Analysis modes

### Baseline PPFD

Evaluates the lighting system on a plant-free horizontal reference plane and reports:

- mean, minimum, and maximum PPFD;
- coefficient of variation and uniformity ratios;
- CSV output;
- PPFD heatmaps and fixture-layout overlays;
- an interactive 3D PPFD scatter plot; and
- baseline PPFD evaluated at representative leaf positions.

### Baseline + Multispectral FSPM

Runs the same baseline first, then introduces the deterministic plant population and evaluates modeled light transport across:

- blue: 400–499 nm;
- green: 500–599 nm;
- orange: 600–624 nm; and
- red: 625–699 nm.

Optional far-red transport covers 700–750 nm and remains separate from PAR and PPFD.

## How it works

1. The selected lighting system is placed in the requested room using its supported fixture-layout rules.
2. Radiance evaluates the plant-free horizontal PPFD field.
3. For LED systems, the source output is adjusted to the requested target within the modeled full-output limit. HPS remains fixed-output.
4. If FSPM analysis is selected, the achieved source state is reused without re-optimizing it, and light is traced over the modeled plant surfaces by spectral band.
5. The application validates the resulting artifacts before publishing the metrics and visualizations.

## Installation

### Requirements

- Python 3.11 or newer.
- A modern browser with WebGL support.
- A local Radiance installation available on `PATH` for `--live` mode.

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Radiance is not required for precomputed playback.

## Running the application

### Precomputed playback

```bash
fspm-optics
```

This is the default public mode. It loads the repository's authenticated precomputed results and does not execute Radiance.

### Live simulation

```bash
fspm-optics --live
```

Live mode runs the supported simulations locally and requires Radiance. Both modes bind to `127.0.0.1:8895` by default.

Equivalent module commands are also available:

```bash
python -m fspm_optics.web
python -m fspm_optics.web --live
```

## Results and visualization

Completed analyses can include:

- PPFD metrics and CSV data;
- validated heatmaps and fixture overlays;
- interactive PPFD scatter visualization;
- a 3D room, fixture, and plant-layout viewer;
- Target Coverage classification at representative leaf positions; and
- aggregate multispectral plant-surface light metrics.

Scientific data and display derivatives are kept separate. Heatmaps, color scales, browser geometry, and other visualization settings do not modify the underlying simulated values.

## Testing

The repository provides maintained test commands for progressively broader coverage:

```bash
scripts/test-fast
scripts/test-subsystem
scripts/test-full
scripts/test-failed
```

See [`tests/TESTING.md`](tests/TESTING.md) for test organization and contribution requirements.

## Scientific scope and limitations

- Results are simulated, not measured.
- The project has not yet been physically validated against a crop, room, or instrument campaign.
- The plant model represents a static, deterministic juvenile Rex lettuce reference at approximately nine days after transplant.
- Plant geometry does not grow or respond to light, time, environment, or prior state.
- The baseline PPFD plane is not equivalent to leaf-incident or leaf-absorbed light.
- Leaf optical behavior uses modeled spectral absorptance, transmittance, and reflectance.
- Far-red is reported separately from PAR and PPFD.
- The engine does not predict photosynthesis, biomass, yield, thermal behavior, driver behavior, or agronomic outcomes.
- Comparator results apply only to the modeled source, geometry, spectral, and operating assumptions included in the repository.

## Research context

The Modularized LED System is related to [U.S. Patent No. 10,687,478, “Optimized LED Lighting Array for Horticultural Applications”](https://patents.google.com/patent/US10687478B2/en/) and to a solo-authored manuscript currently under peer review in *Lighting Research & Technology*.

The patent provides context for the modular topology. It does not expand the scientific claims or validation status of the software.

## Documentation

Detailed scientific and implementation documentation is available in [`docs/`](docs/), including:

- [Juvenile Rex geometry model](docs/plants/rex_juvenile_preheading_geometry_model.md)
- [FSPM surface-light aggregation](docs/plants/fspm_surface_light_aggregation.md)
- [Scientific plant viewer](docs/viewer/rex_juvenile_scientific_viewer.md)
- [Compact precomputed playback bundles](docs/precomputed/compact_playback_bundle_v1.md)

## Project status

The Horticulture Lighting Simulator is pre-1.0 research software, currently version `0.1.0`. Its validation framework checks internal consistency and reproducibility within the declared model; it is not evidence of external physical validation.

## License

The Horticulture Lighting Simulator is licensed under the [BSD 3-Clause Clear License](LICENSE). See [PATENTS.md](PATENTS.md) for the patent notice. Vendored Three.js files retain their upstream MIT terms in [`THREE-LICENSE.txt`](src/fspm_optics/resources/viewer/vendor/THREE-LICENSE.txt).
