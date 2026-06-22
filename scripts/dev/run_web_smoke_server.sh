#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-src}"
export RAD_REBUILD_TRUSTED_HOSTS="${RAD_REBUILD_TRUSTED_HOSTS:-127.0.0.1,localhost}"

if [[ -x "./.venv/bin/python" ]]; then
  exec ./.venv/bin/python -m rad_rebuild.web.app
fi

exec python3 -m rad_rebuild.web.app
