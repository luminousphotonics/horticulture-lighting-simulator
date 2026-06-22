# Architecture Baseline

Horticulture Lighting Simulator is a source-checkout Python application with a
Flask demo around a Radiance-based horticultural lighting simulation workflow.
The internal Python package remains named `rad_rebuild`.

## Runtime Surfaces

- `src/rad_rebuild/web/app.py` owns the Flask application.
- `src/rad_rebuild/radiance/backend/` owns API routes, request models,
  environment handling, workspace paths, job dispatch, and artifact responses.
- `src/rad_rebuild/radiance/engine/` owns source generation, geometry,
  photometry, simulation playback, optimization, validation, and visualization.
- `src/rad_rebuild/layout/` owns layout generator compatibility modules.
- `data/radiance/` contains source curves, IES files, and committed public
  precomputed demo bundles.
- `scripts/radiance/` provides local and Docker-assisted execution helpers.
- `docker/radiance/` provides the live Docker runtime image and its runtime
  requirements export.

## Execution Modes

- Public simulator mode uses precomputed playback by default.
- `rad-rebuild-dev` starts the FastAPI backend and Flask web app together for
  local development.
- Live local Radiance and live Docker Radiance are trusted development modes
  gated by launcher flags and environment configuration.
- Expanded precomputed sweeps are generated or downloaded outside normal source
  edits.

## Protected Contracts

- Active lighting modes: `SMD`, `competitor_practical`, and `hps_karma_4x4`.
- Public route contracts and query handling covered by tests.
- Artifact names, layout JSON shapes, power summaries, manifest fields, and
  PPFD map semantics.
- Import boundaries that keep web/backend code from depending on local runtime
  state at import time.
- Safe archive handling for precomputed release downloads.
- Flask and FastAPI remain separately supervised in deployment-style topologies.

## Validation Baseline

Focused behavior tests are run with:

```bash
PYTHONPATH=src python -m pytest -q tests
```

The durable local quality gate is:

```bash
npm ci
PYTHONPATH=src python scripts/dev/baseline.py
```

Update this document only when architecture, ownership, or public contracts
intentionally change.
