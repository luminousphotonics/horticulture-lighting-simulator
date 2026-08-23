# Compact precomputed playback bundle v1

This document defines the consumer allowlist for compact precomputed playback.
The native completed runtime is an authenticated export source, not a directory
template: exporters must select the payloads below and must never copy the
completed workspace wholesale.

The bundle is a deterministic ZIP with schema ID
`fspm-optics.compact-precomputed-playback-bundle` and schema version `1`.
Every entry has a fixed timestamp and mode, entries are lexically ordered, JSON
is canonicalized where the compact contract owns it, and compression is
lossless DEFLATE. Re-exporting the same completed run must produce identical
bytes and the same overall SHA-256 identity.

## Public consumer allowlist

| Consumer | Retained fields or payload | Authority and validation |
| --- | --- | --- |
| Metrics and result panel | Pruned public-result fields read by the shared `resources/web/result-view.js` renderer, including aggregate Stage B absorption metrics and optional far-red aggregate metrics | Authoritative published aggregates; native publication is validated before export, payload hash/size is checked, and capability state is re-derived during load |
| Final Baseline PPFD download | One little-endian Float64 Stage A table with `x_m`, `y_m`, `z_m`, and `ppfd_umol_m2_s` | Authoritative numeric array; `ppfd.csv` is derived with the live filename, MIME type, header, ordering, row count, and `.17g` values |
| PPFD Heatmap | `ppfd-heatmap.png` plus visualization labels, ranges, orientation, normalization, and field identity | PNG and metadata are authoritative display derivatives; both are hashed and metadata must bind to the Float64 Stage A identity |
| PPFD Heatmap with overlay | `ppfd-heatmap-overlay.png` plus the same visualization metadata | Authenticated display derivative of the Stage A field and authoritative fixture overlay; bytes are copied exactly and hashed |
| Validated PPFD Scatter | Axes, ranges, labels, units, display transforms, and normalization from visualization metadata | Float32 points are derived from the single Float64 Stage A table and must reproduce the live scatter hash, length, count, and source-field identity exactly |
| Plant Layout 3D | Normalized scene metadata, plant transforms, profile, canonical plant GLB, identity map, receiver display buffer, fixture catalog, and run-specific fixture transforms | Run-specific authoritative metadata plus lossless display derivatives; load rechecks all embedded hashes, lengths, counts, strides, profile/identity links, catalog links, and transforms |
| Leaf coloring | Target Coverage reference, tolerance, palette, interpolation, canonical leaf identities, and the single Stage A table | Derived public view; scene and baseline-leaf contracts must bind to the canonical Stage A identity and matching system |
| Public control artifacts | Exact target-control, full-output schedule, operating-point, physical-source-state, natural-fit layout, and baseline leaf-position uniformity JSON | Authoritative publication artifacts; retained byte-for-byte and cross-checked against scientific identities |
| Static viewer code and fixture geometry | Package/resource path, byte size, and SHA-256 for each fixture asset | Referenced immutable catalog assets; packaged bytes are authenticated when materialized, while browser code, Three.js, and fixture GLBs are not duplicated per bundle |

The exact metrics allowlist is: schema/run/system identity, lighting-target
mode, mounting height, analysis scope, runtime status, requested and achieved
target values, cap state, dimming, feasibility, power, PPF, counts, room, aisle,
quality, FSPM target policy, baseline leaf-position uniformity, spatial
uniformity, Stage A visualization identity, and FSPM aggregate surface-light
metrics. Proposed layout, ring, control, source, and spectral-basis fields are
retained when present and are absent—not synthesized—for Conventional and HPS.
Within FSPM metrics the retained fields are modeled one-sided area, counts,
PAR and optional far-red aggregate exposure groups, executed band order/state,
and the six values displayed by the absorption panel: crop-total absorbed PAR,
capture efficiency, absorbed PAR per watt, plant CV, plant minimum/mean, and
leaf CV.

The public 3D leaf-coloring capability is exactly `target_coverage`.
Authoritative native Stage B transport and aggregate absorption metrics remain
scientifically valid, but per-patch/per-leaf Incident or Absorbed PAR coloring
arrays are not playback payloads.

## Configuration and identity contract

`manifest.json` records:

- the schema ID and version, run ID, system ID, and overall bundle identity;
- the complete request and ordered room dimensions in feet and meters;
- an authenticated canonical-domain compatibility record containing the
  long-axis-X dimensions, coordinate-frame policy identity, both supported
  rectangular request orders, and the proper rigid rotation (never reflection
  or scaling);
- aisle state and active-domain identity, layout/ring modes, mounting-height
  semantics, Radiance quality, solver/transport policy, spectral basis, analysis
  scope, and far-red requested/executed state;
- scientific run, source state, fixture layout/catalog/assets/occlusion,
  Stage A field, scene, transport, aggregation, publication, and result
  compatibility identities;
- every retained path, byte length, SHA-256, lossless compression policy, and
  named public consumer;
- a capability list, `scientific_numeric_quantization: false`, the exact Stage A
  record layout, `numeric_arrays_stored_once: true`, and explicit declarations
  that only Target Coverage is public and per-leaf Absorbed PAR coloring is not
  retained.

Production mounting defaults are unchanged by this format: Proposed and
Conventional use 18 inches, and HPS uses 24 inches. The 36-inch HPS case
remains a regression-only configuration.

Fixed-sweep exports additionally bind the fixed plan identity, case ID, case
configuration identity, compatibility-input identity, canonical domain, and
the explicit `250.0 µmol/m²/s` plan target. For Proposed and Conventional that
target is also the authenticated `mean_target` lighting request. HPS
remains targetless at the native request boundary and fixed at full output; its
250 value is an authenticated comparison-plan reference and never authorizes
dimming, post-trace scaling, or symmetrization. Metrics, CSV, scatter, and
surface-light results always report achieved modeled values.

This is an additive, separately versioned canonical-domain/case-binding
compatibility contract within compact bundle schema v1. Existing general v1
bundles without a fixed-plan binding remain loadable under the original
contract. A fixed-plan selection fails closed unless the canonical-domain v1
record and fixed-case-binding v1 record exactly match the selected plan, so no
validation rule was silently weakened and no duplicate portrait bundle is
needed.

## Derived LED target playback

Fixed-sweep Proposed and Conventional playback exposes
`linear_target_ppfd_adjustment_v1`. The capability is a derived-view contract,
not an authenticated alternate bundle capability: the selected manifest,
bundle path, case identity, fixed request, and plan remain the 250-PPFD base.
The derived metadata records that base bundle identity and target, requested
target, maximum achievable PPFD, desired/maximum/applied scales, effective
output fraction, actual achieved mean, saturation state/reason, and a stable
derived-playback SHA-256. Changing the request changes only the derived
identity. HPS does not expose this capability and rejects overrides.

The controller scales from the authenticated 250 operating point and caps at
the authenticated full-output fraction. Alternate targets are in-memory views;
they are never ZIP entries or sweep destinations. Adjusted Stage A Float64 data
drives freshly derived CSV, Float32 scatter, both heatmaps, and the 3D Target
Coverage scalar texture in the canonical landscape display frame. Request
dimension order is provenance only. Aggregate multispectral, far-red,
absorption, PPF, effective power, and physical-unit
statistics follow the same linear scale; variance follows scale squared.
Dimensionless geometry, CV/uniformity, spectral fractions, capture efficiency,
and applicable per-watt metrics remain unchanged. Full-output values remain as
capacity authority.

Target-dependent outputs are recomputed. Target Coverage uses the requested
lighting target and the authenticated tolerance, while automatic FSPM metadata
uses actual achieved mean. Above-capacity playback is successful but explicitly
`output_limited: true` with reason `maximum_output`. At target zero, effective
intensity/power/absorption values are zero and all JSON/display derivatives are
finite. Spatial ratios use deterministic zero; baseline leaf-position CV uses
the existing unavailable/null zero-mean convention.

## Explicit exclusions

Compact bundles exclude Radiance octrees and ambient caches, raw/basis matrices,
RAD fragments, temporary inputs, logs, command transcripts, locks, process
metadata, absolute paths, timestamps, raw Stage A/Stage B intermediates,
per-patch/per-leaf surface-light display arrays, duplicated browser resources,
and immutable fixture GLBs. No scientific numeric array may be rounded,
downcast, or lossy-compressed to save space.

## Validation, loading, and atomic publication

`validate_compact_bundle` is the single classification authority. It verifies
safe archive names, a closed payload inventory, hashes and byte lengths, schema,
the overall identity, public-result agreement, scientific identities, Stage A
and visualization identities, and optional expected run configuration and
scientific identities. It distinguishes:

- valid completed bundle;
- missing bundle;
- interrupted/partial bundle;
- corrupt or tampered bundle;
- incompatible schema;
- mismatched run configuration; and
- mismatched authenticated scientific identities.

Playback data is never returned before validation completes. Export writes a
unique sibling `.partial` file, closes and fsyncs it, validates it through a
fresh reader, then atomically replaces the destination. A failed export never
promotes its temporary file.

Historical precomputed directory bundles are intentionally not interpreted as
v1. They return `incompatible_schema` and require a separately selected legacy
loader. There is no silent fallback or best-effort recovery.

The sole production publisher using this contract is the authenticated fixed
24-case plan documented in
[`fixed_precomputed_sweep_v1.md`](fixed_precomputed_sweep_v1.md). It can export
only after a native run reaches the existing validated completed-runtime state.
