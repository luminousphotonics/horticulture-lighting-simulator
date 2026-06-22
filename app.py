#!/usr/bin/env python3
"""Compatibility launcher for the migrated rad_rebuild Flask app."""

import sys
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rad_rebuild.web.app import app, create_app, main  # noqa: E402,F401


if __name__ == "__main__":
    main()
