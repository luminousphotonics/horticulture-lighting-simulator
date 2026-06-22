# Boundary Security Controls

Public requests stay in the precomputed playback path by default. Boundary
validation rejects malformed input before it reaches live execution or
filesystem-sensitive code.

## Request Limits

- `RADIANCE_MAX_REQUEST_BYTES` sets the FastAPI and Flask request body limit. The default is `65536` bytes.
- Public numeric fields reject strings, NaN, Infinity, negative zero where invalid, and values outside the configured domain limits.
- Unknown JSON fields are rejected by the FastAPI request models.

## CORS

The FastAPI backend does not use wildcard CORS. By default it allows the local same-origin Flask UI origins:

- `http://127.0.0.1:5001`
- `http://localhost:5001`

Set `RADIANCE_CORS_ALLOW_ORIGINS` to a comma-separated allowlist for deployed proxy topologies.

## Live Execution

Live Docker and local Radiance execution are disabled by default. Set `RADIANCE_ENABLE_LIVE_EXECUTION=1` only in trusted environments. When enabled, live starts are rate-limited per session.

Hosted production must set `RAD_REBUILD_DEPLOYMENT_MODE=production`. Production
mode forces `RADIANCE_PRECOMPUTED_MODE=only`, disables live execution even when
live environment variables are accidentally present, and suppresses Docker/local
Radiance runtime status autodetection.

## Public Errors

FastAPI and Flask boundary errors return stable public error codes/messages with an `X-Correlation-ID` response header. Detailed exceptions are logged server-side.

## Precomputed Downloads

Remote precomputed archives must use HTTPS and a configured SHA-256 checksum. Archives are downloaded with a timeout and maximum byte count, then extracted into a private staging directory. Tar and zip members are rejected when they contain traversal paths, absolute paths, duplicate/conflicting names, links, devices, FIFOs, unsafe permissions, or excessive file counts/sizes.
