# Phase 23A Conventional scalar transport policy

Phase 23A executes one fixed-output, horizontal scalar-PAR/PPFD workflow for
the approved Conventional LED comparator. It connects the validated Phase 22
profile, practical layout, derived IES, and aperture adapter to native Radiance.
It does not add a second calibration path.

## Native sequence

The headless execution sequence is fixed:

1. Validate the typed request and reject a repository-local or nonempty run
   root.
2. Plan the Conventional layout, scalar room, horizontal sensor grid, standard
   Radiance options, and all scientific identities without writing files.
3. Write the deterministic downward-normalized derived IES.
4. Run the existing neutral Phase 22C command plan through `CommandSpec` and
   `LocalRunner`: `ies2rad -dm -t default -c 1 1 1 -m 307164 -o
   conventional_led_unit_downward_flux conventional_led_unit_downward_flux.ies`.
5. Require exactly the planned RAD and DAT outputs. Validate the two-dimensional
   finite nonnegative DAT table and the complete centered eight-primitive
   `boxcorr` RAD contract.
6. Write the validated shared `flatcorr` angular definition and one explicit
   downward aperture polygon per fixture. The raw box is excluded from the
   scene.
7. Compile the ordered room, shared angular source, and aperture files with
   `oconv -f ...`, routing binary stdout to the dedicated octree.
8. Run ASCII `rtrace -h -I+ -n THREADS` with the standard option profile and a
   Conventional-only ambient cache. Sensor rays enter through stdin and raw RGB
   leaves through the planned output file.
9. Require exactly three finite, nonnegative, equal scalar channels for every
   expected sensor row. Decode the equal-channel value directly to PPFD and
   write the successful scientific artifacts.

All commands use explicit argv/cwd/stream paths and `shell=False`. No `xform`,
inline Radiance command, environment configuration, server, or Proposed SMD
execution is part of this path. Provenance uses the supported `rtrace` version
boundary; `oconv -version` is never attempted.

`ies2rad` runs in `conventional_scalar/source/`, where it writes the raw RAD and
DAT pair. Both `oconv` and `rtrace` run in the `conventional_scalar/` run root.
The final adapted `brightdata` therefore references the existing DAT as
`source/conventional_led_unit_downward_flux.dat`. This reference is derived
from the typed workspace plan; the DAT is neither moved nor copied. Before
`rtrace`, the workflow resolves that reference from the actual command cwd,
requires a nonempty file, rechecks its validated hash, and confirms the adapted
RAD contains the planned reference. `source.cal` remains a canonical Radiance
library resource.

## Fixed operating point and exactly-once scaling

Each fixture is rated at 660 W, 2.6 µmol/J, and 1,716 µmol/s downward
transported PAR PPF. The derived Type-C downward angular integral is one.
`ies2rad -m 307164` supplies `1,716 × 179`; native DAT generation applies the
canonical `1 / WHTEFFICACY` conversion. The final flat-source correction is
`307164 / (1.190 × 1.087)` and is applied exactly once.

There is no second 179 conversion, post-trace scaling, or source
reconciliation. Application target control may use a full-output preliminary
trace, but the published result comes from a final transport rerun whose
source carrier, effective power, and effective PPF have all been scaled by the
one global dimming factor. Original candela multiplier, ballast factor,
the 1.10000 future-use field, tested watts, raw luminous magnitude, and SPD
amplitude are diagnostic provenance only.

The original IES has a small upward fraction (about 0.197446%). Its full,
downward, and upward diagnostics are retained, but the upward fraction is
excluded from the one-sided downward model. The model is not a full-sphere
reproduction of the tested fixture.

## Default closure

The default CLI scenario is a 10 ft × 10 ft room, `practical` layout, 0.4572 m
mount height above the horizontal reference plane, the standard adaptive sensor
grid, the standard Radiance quality profile, and six threads. Its acceptance
gates are:

- 2 × 2 fixtures and exactly four downward apertures;
- 2,640 W total rated power;
- 6,864 µmol/s total planned downward PPF;
- one unit derived downward angular integral;
- no plants, raw box geometry, bar geometry, SMD emitters, dimming, or target;
- matching source, layout, room, sensor, quality, scene, octree, ambient-cache,
  and result identities.

Other legal rectangular room dimensions and explicit positive thread counts use
the same fixed profile and closure rules. No sweep infrastructure is provided.

## Workspace and artifacts

The explicit workspace owns a `conventional_scalar/` run root. A run refuses to
reuse a nonempty root, so a DAT, RAD, octree, ambient cache, or result from other
physics cannot be silently accepted. Failed runs preserve raw/log artifacts and
write `failure_summary.json`; they do not write a successful scalar summary.

Successful artifacts include:

```text
conventional_scalar/
  input_identity.json
  source_conversion_summary.json
  scalar_transport_summary.json
  command_provenance_summary.json
  ppfd_values.npz
  source/                         # derived IES, raw RAD/DAT, adapted source
  scene/                          # room, apertures, sensor rays, scene manifest
  native/                         # octree, ambient cache, raw rtrace RGB
  logs/                           # captured native stderr
```

The source summary records original/derived/RAD/DAT hashes, hemisphere
diagnostics, excluded upward fraction, carrier derivation, normalized `ies2rad`
argv, flat correction, aperture area/count, and claim boundary. The scalar
summary records success, scientific identities, PPFD metrics, room/layout/mount
semantics, fixed operating point, quality/thread settings, native hashes,
Radiance provenance, and limitations. `ppfd_values.npz` stores deterministic
`x_m`, `y_m`, `z_m`, and `ppfd_umol_m2_s` float arrays in stable sensor order.
Scientific identity hashes exclude timestamps and machine-specific paths.

## CLI and manual native validation

```text
fspm-optics conventional-scalar-transport \
  --workspace /absolute/runtime/workspace \
  --room-ft 10 10 \
  --threads 6
```

`--workspace` is required. `--room-ft` defaults to `10 10`, and `--threads`
defaults to the existing local thread count. The command executes the native
flow, prints a concise JSON summary on success, and returns nonzero on failure.

Manual validation must use an external empty workspace and a supported native
Radiance installation, inspect all three stderr logs, confirm the command and
scene manifests, confirm the four-fixture 3,200 W / 8,960 µmol/s closure, and
review the PPFD metrics and hashes. Automated tests use only injected fake
runners and synthetic outputs; they do not require Radiance.

## Scientific claim boundary

Phase 23A reports modeled horizontal PPFD for the declared fixed-output
Conventional comparator. It makes no claim about photosynthesis, DLI, biomass,
yield, growth, absorbed photons, Rex leaf response, or comparison with the
Proposed SMD system. Conventional Rex receivers, five-band transport, formal
system comparison, visualization, optimization, and sweeps remain out of scope.
