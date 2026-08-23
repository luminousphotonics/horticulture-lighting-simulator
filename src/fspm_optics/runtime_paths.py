"""Shared constants and read-only checks for managed application runtime paths."""

from __future__ import annotations

from pathlib import Path

DEFAULT_RUNTIME_DIRECTORY = ".fspm-optics-runtime"
MANAGED_MARKER_NAME = ".managed-runtime-v1"
MANAGED_MARKER_CONTENT = "fspm-optics-managed-runtime\nversion=1\n"
MANAGED_LOCK_NAME = ".managed-runtime.lock"
ARTIFACT_DIRECTORY_NAMES = ("staging", "completed", "failed")


def default_runtime_root(repository_root: str | Path) -> Path:
    """Return the one repository-contained runtime root the application permits."""

    return Path(repository_root).expanduser().resolve() / DEFAULT_RUNTIME_DIRECTORY


def is_default_managed_runtime_descendant(
    path: str | Path,
    repository_root: str | Path,
) -> bool:
    """Return whether a path is below a valid managed artifact directory.

    This is intentionally read-only.  It lets lower-level output boundaries retain
    their repository exclusion while recognizing workspaces allocated by the
    application under its one marked repository-local runtime root.
    """

    repository = Path(repository_root).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    root = default_runtime_root(repository)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    if len(relative.parts) < 2 or relative.parts[0] not in ARTIFACT_DIRECTORY_NAMES:
        return False
    marker = root / MANAGED_MARKER_NAME
    if root.is_symlink() or marker.is_symlink():
        return False
    try:
        if marker.read_text(encoding="utf-8") != MANAGED_MARKER_CONTENT:
            return False
    except (OSError, UnicodeError):
        return False
    managed_parent = root / relative.parts[0]
    return managed_parent.is_dir() and not managed_parent.is_symlink()
