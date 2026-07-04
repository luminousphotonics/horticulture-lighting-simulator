# API And Artifact Contracts

This document records the public JSON and artifact contracts that already exist in
the Radiance backend. It is descriptive: it does not redesign artifact formats,
bundle schemas, scientific outputs, status codes, response keys, or frontend
behavior.

## Public JSON API

- Public JSON success responses use explicit Pydantic response models at the
  FastAPI boundary. Nested scientific payloads remain opaque JSON objects when
  their format is already owned by an artifact schema.
- Public error responses use one envelope: `ok: false`, `error`, `message`,
  `correlation_id`, and optional `detail`. `detail` is included only when the
  raised `HTTPException.detail` is already a dictionary.
- `X-Correlation-ID` and `X-Request-ID` are request-scoped identifiers. A valid
  32-character lowercase hexadecimal `x-correlation-id` request header may be
  reused; otherwise the backend generates one.
- `docs/api/radiance-openapi.json` is the committed deterministic OpenAPI
  export. Refresh or check it with `PYTHONPATH=src python scripts/dev/export_openapi.py`
  or `PYTHONPATH=src python scripts/dev/export_openapi.py --check`.

## Schema Versions

- Radiance visualization manifests use `schema_version: 2`. They describe the
  rendered mode, visualization directory, geometry and material files, renderer
  parameters, PPFD grid outputs, renderer inputs, scale factors, SPD/generator
  provenance, and HPS profile/calibration metadata when applicable.
- Request fingerprints use `schema_version: 1`. They canonicalize the public
  request fields that select a workspace and artifact identity.
- Workspace records use `schema_version: 1`. They bind a session, request
  fingerprint, runtime identity, and committed workspace path.
- Artifact grant tokens use `schema_version: 1`, encoded in the public token
  prefix as `v1.`. Grant records store the session, request fingerprint, token
  SHA-256, and issue time.
- Precomputed bundle manifests use `schema_version: 1`. They define bundle mode,
  dimensions, request parameters, and required artifact paths for playback.
- SMD runtime fingerprints use `schema_version: 1`. They compare emitter
  environment, layout, curve model, sensor grid, basis manifest, and basis hash
  provenance before reusing solved powers.

## Precomputed Bundle Storage

- The repository should only track the small public demo bundle set. Expanded
  precomputed sweeps, including plant-enabled sweeps, must be generated into an
  external dataset root with `--dataset-root`.
- Plant-enabled precomputed sweep planning uses a canonical plant contract for
  rows, columns, spacing, receiver granularity, and scalar FSPM spectral
  transport identity. Lighting target PPFD, FSPM target PPFD, and FSPM target
  tolerance are runtime controls and are not bundle identity fields.
- Plant-enabled bundles may carry compact plant receiver artifacts using
  `rad_rebuild.precomputed.plant_receiver.v1`. New scalar precomputed bundles
  store receiver rows as columnar `plant_receiver.npz`; SMD bundles store the plant
  receiver basis as `plant_receiver_basis_A.npz`. Conventional artifacts store
  full-output raw plant PPFD values that playback scales with the lighting
  target; HPS artifacts store fixed-output plant PPFD values; SMD artifacts align
  the plant receiver basis with the SMD canopy basis columns so playback reuses
  the solved basis coefficients. The receiver NPZ keeps only stored scalar PPFD
  values, compact receiver-to-surface identity columns, and small JSON metadata
  needed for playback; runtime target-capped metrics are recomputed during
  playback and are not the source of truth. Older `plant_receiver.json.gz` and
  `plant_receiver.json` bundle artifacts remain playback-compatible fallbacks.
- Current plant-enabled precomputed bundles are scalar-only. They intentionally
  include scalar receiver/surface-flux data for playback, target/tolerance
  metrics, coloring, and 3D mapping, but exclude modeled spectral absorption,
  spectral response, photosynthesis, photoreceptor, and photomorphogenesis
  artifacts. Multispectral precomputed bundles will need a separate artifact
  contract later. The columnar receiver artifact is the canonical scalar bundle
  source; playback expands it to runtime `plant_receiver.json` and derives
  the runtime `plant_surface_flux.json` used by 3D surface-flux coloring without
  requiring a `plant_surface_flux_json` bundle manifest key. Target PPFD and
  tolerance remain runtime controls.
- Full sweep commands should be handed to the local operator to run later. Codex
  should not run real full-sweep generation or commit generated bundles.

## Provenance Fields

- File entries use `path`, `sha256`, and `bytes` to bind JSON responses to exact
  workspace files.
- Visualization manifests expose `spd_hash` and `generator` for the source file
  that generated the mode-specific emitter/material data.
- Renderer provenance includes `renderer_params`, `renderer_inputs`, and
  `scale_factor`; these fields explain how a rendered artifact was produced and
  must not be silently reinterpreted.
- HPS manifests include `hps_profile` and `calibration_sensitive_elements`.
  These fields identify the profile and calibration inputs that make HPS output
  comparable across runs.
- Precomputed playback verifies declared artifact paths and basis hashes before
  materializing data into a workspace.

## Compatibility Rules

- Preserve public JSON keys, values, status codes, and frontend-visible URLs.
  Adding response models must not filter existing keys or inject new `null`
  fields into nested scientific payloads.
- Treat manifest, metrics, cost estimate, and precomputed bundle internals as
  protected artifact contracts. Type the public envelope, not the scientific
  payload shape, unless a separate reviewed artifact-schema change authorizes it.
- Increment a schema version only for an intentional incompatible change to that
  schema. Do not change committed source curves, IES data, numerical goldens, or
  public demo bundles as part of API contract typing work.
- Keep OpenAPI exports deterministic: sorted JSON keys, stable indentation, and
  no runtime-specific paths or timestamps.
