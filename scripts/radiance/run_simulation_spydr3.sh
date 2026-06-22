#!/usr/bin/env bash
# Compatibility entry point for the conventional LED Radiance simulation.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)"

PY="${PY:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${PY}" ]]; then
	PY="${PYTHON:-python3}"
fi

export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec "${PY}" -m rad_rebuild.radiance.cli.scripts run-simulation-spydr3 "$@"
