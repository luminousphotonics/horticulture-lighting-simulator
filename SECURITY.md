# Security Policy

## Supported Versions

Security fixes are accepted for the current public `main` branch and the latest
tagged release.

## Reporting A Vulnerability

Please report suspected vulnerabilities privately by emailing the maintainer or
opening a private GitHub vulnerability report if that feature is available.
Include:

- affected commit, release, or branch
- reproduction steps or proof of concept
- expected impact
- any relevant logs, request payloads, or environment details

Please do not open a public issue for an unpatched vulnerability.

## Project Boundaries

Live local Radiance and live Docker execution are development features and are
disabled by default. Public deployments should keep browser traffic behind the
Flask same-origin proxy and expose the FastAPI backend only to trusted internal
networks.

Dependency audit waivers must be narrow, documented, owned, and time-limited in
`audit/pip-audit-allowlist.toml`.
