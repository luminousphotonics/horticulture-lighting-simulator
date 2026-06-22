# Render Deployment

This repo deploys the simulator separately from the marketing site. Use it for
`sim.luminousphotonics.com` or another simulator-only host; keep
`luminousphotonics.com` in the portfolio repo.

## Service Shape

The Render web service runs one public Flask/Gunicorn web process and one
private FastAPI backend process from the same container. The web process proxies
browser API traffic to the backend over `127.0.0.1`.

Use the included `render.yaml` blueprint or configure the service manually:

```bash
python -m pip install -e ".[web,api,scientific]"
```

```bash
python scripts/deploy/render_start.py
```

Set the health check path to:

```text
/healthz
```

## Required Environment

Production must be precomputed-only:

```text
RAD_REBUILD_DEPLOYMENT_MODE=production
RAD_REBUILD_SHOW_LIVE_MODES=0
RADIANCE_ENABLE_LIVE_EXECUTION=0
RADIANCE_USE_DOCKER=0
RADIANCE_PRECOMPUTED_MODE=only
```

The production mode is authoritative. If a live flag is accidentally set, the
backend still reports and enforces `live_execution_enabled=false`.

Set the public host allowlist before routing traffic:

```text
RAD_REBUILD_TRUSTED_HOSTS=sim.luminousphotonics.com
```

Include the temporary Render hostname too if you need to test before DNS is
cut over.

## Persistent Disk

Attach a small persistent disk. The blueprint mounts it at `/var/data` and uses:

```text
RENDER_DISK_PATH=/var/data
RADIANCE_OUTPUT_ROOT=/var/data/runtime
RADIANCE_PRECOMPUTED_ROOT=/var/data/precomputed
```

Runtime cleanup is enabled through the existing backend cleanup loop:

```text
RADIANCE_SESSION_ARTIFACT_RETENTION_S=3600
RADIANCE_RUNTIME_CLEANUP_INTERVAL_S=300
RADIANCE_COMPLETED_JOB_RETENTION_S=1800
```

Those settings keep per-session grants, staged workspaces, file caches, and
completed jobs from growing without bound on the Render disk.

## Precomputed Bundles

Hosted production does not require Docker, local Radiance, or private IES
assets. It serves public precomputed playback bundles only.

The repository includes public demo bundles. To hydrate the expanded public
10-30 ft release bundles on startup when the disk is empty, set:

```text
RAD_REBUILD_PRECOMPUTED_AUTO_DOWNLOAD=1
```

The launcher checks `RADIANCE_PRECOMPUTED_ROOT` for all expected public bundle
families before downloading. Downloads use:

```bash
python scripts/radiance/download_precomputed.py --dataset full --dest "$RADIANCE_PRECOMPUTED_ROOT" --force
```

Remote archives must use HTTPS and the SHA-256 checksums configured in
`scripts/radiance/download_precomputed.py`.

## Frame Policy

The simulator is intended to be linked from the portfolio site, not embedded in
a cross-origin iframe. Normal pages use `frame-ancestors 'none'`.

Same-origin simulator internals that are intentionally framed by the app remain
frameable by `'self'`. To allow a specific external embed origin, set:

```text
RAD_REBUILD_FRAME_ANCESTORS=https://luminousphotonics.com
```

Do not use wildcard frame ancestors.
