from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


_VALID_IDENTIFIER_CHARS = re.compile(r"[^A-Za-z0-9_]+")
_DIGEST_HEX_CHARS = 24


def _canonical_value(value: Any) -> str:
    if isinstance(value, bool):
        return f"bool:{int(value)}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Radiance identifier values must be finite.")
        normalized = 0.0 if value == 0.0 else value
        return f"float:{normalized:.17g}"
    if isinstance(value, str):
        return f"str:{value}"
    if isinstance(value, Path):
        return f"path:{value.as_posix()}"
    if value is None:
        return "none:null"
    if isinstance(value, tuple):
        return "tuple:" + json.dumps([_canonical_value(item) for item in value], separators=(",", ":"))
    if isinstance(value, list):
        return "list:" + json.dumps([_canonical_value(item) for item in value], separators=(",", ":"))
    raise TypeError(f"Unsupported Radiance identifier value type: {type(value).__name__}")


def radiance_identifier(prefix: str, *values: Any) -> str:
    clean_prefix = _VALID_IDENTIFIER_CHARS.sub("_", prefix.strip()).strip("_") or "rad"
    payload = json.dumps([_canonical_value(value) for value in values], separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_DIGEST_HEX_CHARS]
    return f"{clean_prefix}_{digest}"
