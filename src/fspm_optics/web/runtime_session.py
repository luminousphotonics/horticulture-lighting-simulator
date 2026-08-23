"""Side-effect-free runtime-root resolution for the local application CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from fspm_optics.runtime_paths import default_runtime_root

from .workspaces import RuntimeWorkspaces


@dataclass(frozen=True, slots=True)
class RuntimeRootResolution:
    path: Path
    automatic_session: bool


def resolve_runtime_root(
    runtime_root: str | Path | None,
    repository_root: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    fallback_directory: str | Path | None = None,
) -> RuntimeRootResolution:
    """Resolve an explicit root or the exact repository-local default.

    Resolution never writes to the filesystem.  ``environ`` and
    ``fallback_directory`` remain accepted for caller compatibility but are
    deliberately ignored; OS runtime, cache, and temporary locations are not
    candidates.
    """

    repository = Path(repository_root).expanduser().resolve()
    if runtime_root is not None:
        validated = RuntimeWorkspaces(runtime_root, repository)
        return RuntimeRootResolution(validated.root, automatic_session=False)
    del environ, fallback_directory
    validated = RuntimeWorkspaces(default_runtime_root(repository), repository)
    return RuntimeRootResolution(validated.root, automatic_session=True)
