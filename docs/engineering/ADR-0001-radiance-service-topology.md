# ADR 0001 - Radiance Web Service Topology

## Status

Accepted.

## Context

The public Flask web app proxies browser requests to a FastAPI Radiance backend. Previous Flask code could start, stamp, and terminate the backend from request handling. That behavior is unsafe under real WSGI workers because multiple workers can race over PID files, kill a backend owned by another worker, or create child processes during public request handling.

The FastAPI backend owns durable job execution, repositories, cleanup, and readiness. Those dependencies need one explicit application lifecycle per ASGI worker rather than module-level startup assumptions.

## Decision

Run Flask and FastAPI as separately supervised services.

- Flask is the public WSGI app. It serves the simulator templates, keeps small layout export adapter endpoints for compatibility, and same-origin proxies `/radiance-api/*` to the internal FastAPI service.
- FastAPI is the internal ASGI app. It binds to `127.0.0.1` by default, owns Radiance job lifecycle through its lifespan context, and exposes `/health`, `/ready`, and `/metrics`.
- Supervisors, not request handlers, start and stop both services.
- `RADIANCE_RESEARCH_API_BASE` may point Flask at a non-default internal backend URL. If unset, Flask uses `http://127.0.0.1:8786`.
- CORS is disabled by default because browser traffic uses same-origin Flask proxying. Direct backend deployments must configure exact `RADIANCE_CORS_ALLOW_ORIGINS` values.

## Consequences

- Importing Flask or FastAPI modules does not create runtime directories or child processes.
- WSGI workers never create, restart, or terminate FastAPI from a request.
- Multiple app instances can be created in tests with injected proxy clients, settings, and job services.
- Local development may use the supervisor:

```bash
rad-rebuild-dev
```

- The services can also be run separately when exercising deployment-style
  topology:

```bash
PYTHONPATH=src python -m rad_rebuild.radiance.backend.server
PYTHONPATH=src python -m rad_rebuild.web.app
```

## Rollback

Rollback is a source revert of the Flask factory/proxy, FastAPI
factory/lifespan, and deployment entrypoint changes. Runtime state can remain
in place; the durable job store is not migrated by this ADR.
