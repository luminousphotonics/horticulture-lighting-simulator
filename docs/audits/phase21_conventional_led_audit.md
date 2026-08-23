# Phase 21 Conventional LED engineering audit

## Purpose and current boundary

This audit records the engineering decisions carried forward for the
vendor-neutral Conventional LED comparator. Product, manufacturer, laboratory,
test-record, private-path, and superseded source identifiers have been removed.
The current package resources and typed authorities are the sole source of
current-tree identity.

The comparator is a controlled modeled scenario. Its normalized photometric
angular distribution and relative spectral shape are separate from its declared
electrical and photon-output policy. The model must not be described as a
measurement of a particular commercial fixture.

## Preserved scientific conclusions

- The public system identifier is `conventional` and the display label is
  `Conventional LED System`.
- The fixture is modeled as an eight-bar assembly with a normalized LM-63
  angular distribution and an independently declared relative SPD.
- Relative SPD values define spectral shape only. They do not establish power,
  PPF, PPE, or absolute radiant flux.
- The photometric input's tested input watts are provenance-only and do not set
  the modeled operating point.
- The comparison profile declares its electrical power, PPE, and PPF explicitly.
  Scaling the normalized angular distribution to that declared output is a
  scenario assumption, not a product-performance claim.
- Full-output transport is performed once and controlled output uses deterministic
  global Float64 post-trace scaling. The source carrier and the post-trace scale
  must never be applied twice.
- Practical layout pitch is intentionally coupled to the Proposed comparison
  lattice. Rolling-bench layout is a separate explicit policy.
- Mounting height is measured from the emitting aperture plane to the receiver
  reference plane. Fixture-center ambiguity is not permitted in the current
  geometry contract.
- Fixture overlays are display-only geometry. Scientific occlusion and transport
  use authenticated fixture geometry and typed placement transforms.

## Resource contract

The current resource names are:

- `conventional_led_8_bar.ies`
- `conventional_led_spd.csv`
- `fixtures/conventional/conventional_led_8_bar.glb`

The IES resource is sanitized LM-63 text. Its current keywords use generic
manufacturer, catalog, test, and laboratory values. Numerical photometric data,
dimensions, tilt behavior, candela tables, and tested input watts remain the
scientific authority.

The SPD contains 401 ordered samples from 380 through 780 nm at 1 nm spacing.
The missing long-wavelength tail remains explicit zero data rather than an
extrapolation. Photon fractions are computed from relative radiant SPD multiplied
by wavelength and normalized over the defined PAR interval; far-red remains a
separate ratio outside PAR PPF and PPE.

The GLB is authenticated by byte size and SHA-256. Its public registry identity is
`conventional-led-8-bar-v1` with fixture type `conventional_led_8_bar`. Geometry,
materials, classification, and placement correction remain byte- and
numerically stable.

## Architecture decisions retained from the audit

1. Resource loading is package-relative, explicit, and hash checked.
2. LM-63 parsing is strict and independent from Radiance command execution.
3. Angular normalization, source-energy budgeting, layout, mounting geometry,
   source emission, transport, and artifact publication remain separate typed
   stages.
4. Command execution is injected through the runner boundary; library imports do
   not create directories or start subprocesses.
5. Layout construction rejects impossible fits rather than silently changing a
   requested fixture count.
6. Deterministic fixture IDs and Y-major/X-minor ordering are preserved.
7. Public artifacts keep measured input metadata, declared comparison inputs,
   relative-spectrum authority, and source identities in distinct fields.
8. Committed precomputed bundles are immutable historical artifacts. Public
   playback authenticates them canonically, then publishes vendor-neutral typed
   presentation JSON without exposing their historical source metadata.

## Historical defects not carried forward

The superseded implementation mixed environment parsing, global mutable state,
filesystem writes, command execution, layout generation, photometric staging,
and reporting. It also used a lumen/SPD bridge for absolute calibration and
rewrote source geometry after conversion. Those behaviors are reference lessons,
not current authorities.

The current model instead normalizes the validated angular table, applies the
declared PPF once, uses an explicit source carrier, preserves one downward
emission convention, and keeps post-trace target scaling separate. Any change to
these boundaries requires focused identity, energy-closure, layout, mounting,
and playback regression tests.

## Verification expectations

- Recompute current resource hashes only when a resource change is intentionally
  authorized; committed playback identities never depend on that recomputation.
- Keep numerical LM-63 and SPD regression coverage independent of vendor text.
- Verify source energy closure, spectral fraction closure, fixture placement,
  GLB bounds, and occlusion identities with bounded focused tests.
- Keep Conventional and HPS unavailable in live public mode.
- Scan public results, metrics, manifests, availability, status, viewer JSON,
  static application text, asset URLs, and error responses for prohibited source
  vocabulary.
- Serve committed GLB and transform binaries unchanged through generic public
  paths, and reject direct historical archive paths.
