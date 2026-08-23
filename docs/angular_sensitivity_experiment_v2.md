# Angular-sensitivity experiment v2

This experiment is an isolated Stage A study of completed-aperture angular
sensitivity. It does not change the production simulation defaults, playback,
public UI, live-mode behavior, or protected source resources.

## Fixed matrix

The requested/display room order and variant order are fixed:

1. 10×10 ft — native
2. 10×10 ft — altered 140° FWHM
3. 10×10 ft — altered 100° FWHM
4. 30×50 ft — native
5. 30×50 ft — altered 140° FWHM
6. 30×50 ft — altered 100° FWHM

Rooms remain long-axis aligned internally. The experiment-only 0.145 m,
cell-centered sensor policy is pinned to 21×21 for 10×10 ft and 105×63 for
30×50 ft; planning and execution fail closed if either resolution changes.
The reduced-one-ring standalone layouts contain 41 and 711 modules,
respectively.

## Execution and artifacts

Every case uses the production uniform Proposed Stage A implementation with:

- physical composition `standalone_modules`;
- ring mode `reduced_one_ring` (Reduced by one ring);
- control mode `uniform_module_dimming`;
- one complete equal-module scene trace;
- one global dimming factor for the requested mean PPFD;
- zero basis columns and no basis-matrix artifact contract.

The complete-scene inputs, outputs, manifest, and execution record live under
each case's `complete-scene/` directory. The operating point is recorded in
`uniform-control.json`; `case-result.json` authenticates the complete artifact
inventory and explicitly records the quality, module count, dimming factor,
achieved mean PPFD, and electrical power. A completed case is reusable only
with `--resume` and only after its v2 identity and every declared artifact hash
validate. Version 1 plans, cases, output directories, and republish inputs are
incompatible.

The characterization schema is version 3. At each polar-angle/azimuth
direction it traces three adjacent identical receiver rays and takes their
median before the existing weighted azimuth mean. The resulting profile is
normalized to its measured optical-axis intensity, checked with the existing
2-degree filtered-axis plateau contract, and measured at half that
authenticated optical-axis reference. The replicate count, aggregation, and
sample order are authenticated in the output.

Angular CAL behavior, flux normalization, PPE authority, mounting height,
room materials, polar profiles, polar diagrams, and the Stage A-only claim
boundary otherwise remain unchanged. The selected Radiance quality is used
for characterization and all six cases; its default is `standard`.

## Commands

Plan the presentation target without running characterization or Radiance:

```bash
./.venv/bin/python -m fspm_optics.angular_experiment_cli plan \
  --output-dir <fresh-output-directory> \
  --target-ppfd 250 \
  --quality standard \
  --threads 1
```

When the real run is authorized later, use a fresh output directory:

```bash
./.venv/bin/python -m fspm_optics.angular_experiment_cli execute \
  --output-dir <fresh-output-directory> \
  --target-ppfd 250 \
  --quality standard \
  --threads 6
```
