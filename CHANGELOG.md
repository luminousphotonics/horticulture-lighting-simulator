# Changelog

All notable public changes are documented here.

This project uses semantic versioning where practical while the public API is
still stabilizing.

## [0.1.0] - 2026-06-19

Initial public-readiness draft.

### Added

- Flask demo app for precomputed horticultural lighting simulation playback.
- FastAPI Radiance backend with health, readiness, metrics, run, artifact, and
  workspace routes.
- Three active lighting modes: proposed LED `SMD`, conventional LED
  `competitor_practical`, and `hps_karma_4x4`.
- Minimal committed `10x10` demo bundles for public playback.
- Download helper for full precomputed release archives.
- Live local Radiance and live Docker Radiance development execution paths.
- CI gates for pytest, durable baseline checks, package smoke tests, lock/export
  consistency, browser smoke tests, Docker smoke tests, dependency audits, and
  whitespace checks.

### Notes

- The public UI defaults to precomputed playback.
- Live execution requires local Radiance or Docker setup and is intended for
  trusted development environments.
