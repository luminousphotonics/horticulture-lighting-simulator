from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


class ExecutableResolutionError(RuntimeError):
    """Raised when a configured executable cannot be resolved safely."""


def _env_path(env: Mapping[str, str] | None) -> str:
    source = os.environ if env is None else env
    return str(source.get("PATH", ""))


def _validate_executable(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ExecutableResolutionError(f"{label} not found: {path}") from exc
    if not resolved.is_file():
        raise ExecutableResolutionError(f"{label} is not a regular file: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise ExecutableResolutionError(f"{label} is not executable: {resolved}")
    return resolved


def resolve_executable(
    command: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    label: str | None = None,
) -> Path:
    token = os.fspath(command)
    name = label or token
    if not token:
        raise ExecutableResolutionError(f"{name} executable is empty")
    explicit = os.sep in token or (os.altsep is not None and os.altsep in token)
    if explicit:
        path = Path(token).expanduser()
        if not path.is_absolute():
            path = (cwd or Path.cwd()) / path
        return _validate_executable(path, name)

    for entry in _env_path(env).split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / token
        try:
            return _validate_executable(candidate, name)
        except ExecutableResolutionError:
            if candidate.exists():
                raise
            continue
    raise ExecutableResolutionError(f"{name} not found on PATH: {token}")
