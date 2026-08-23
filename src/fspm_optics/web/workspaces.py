"""Managed runtime ownership, locking, cleanup, allocation, and safe reads."""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import IO

from fspm_optics.runtime_paths import (
    ARTIFACT_DIRECTORY_NAMES,
    MANAGED_LOCK_NAME,
    MANAGED_MARKER_CONTENT,
    MANAGED_MARKER_NAME,
    default_runtime_root,
)

LOGGER = logging.getLogger(__name__)

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
VIEWER_SUFFIX_ALLOWLIST = {
    ".html",
    ".css",
    ".js",
    ".json",
    ".glb",
    ".bin",
    ".txt",
}
SCATTER_VIEWER_FILES = {
    "index.html": "ppfd-scatter-viewer/index.html",
    "styles.css": "ppfd-scatter-viewer/styles.css",
    "main.js": "ppfd-scatter-viewer/main.js",
    "presentation-export.js": "ppfd-scatter-viewer/presentation-export.js",
    "vendor/three.module.js": "plant-layout-viewer/vendor/three.module.js",
    "vendor/three.core.js": "plant-layout-viewer/vendor/three.core.js",
    "vendor/addons/controls/OrbitControls.js": (
        "plant-layout-viewer/vendor/addons/controls/OrbitControls.js"
    ),
}


class WorkspaceSafetyError(ValueError):
    """A runtime root, run ID, or requested artifact failed closed."""


class RuntimeRootLockedError(RuntimeError):
    """Another live application server owns the runtime root lock."""


class RuntimeWorkspaces:
    """Own one exact, marked runtime root without import-time filesystem writes."""

    def __init__(self, runtime_root: str | Path, repository_root: str | Path) -> None:
        raw = Path(runtime_root).expanduser()
        if not raw.is_absolute():
            raise WorkspaceSafetyError("runtime root must be an explicit absolute path.")
        if _path_has_symlink_component(raw):
            raise WorkspaceSafetyError(
                "runtime root and its existing path components must not be symlinks."
            )
        self._requested_root = raw
        self.root = raw.resolve()
        self.repository_root = Path(repository_root).expanduser().resolve()
        allowed_repository_root = default_runtime_root(self.repository_root)
        if self.root == self.repository_root:
            raise WorkspaceSafetyError(
                "runtime root must never equal the repository root."
            )
        if (
            self.root.is_relative_to(self.repository_root)
            and self.root != allowed_repository_root
        ):
            raise WorkspaceSafetyError(
                "runtime root must be outside the repository except for the exact "
                f"managed default {allowed_repository_root}."
            )
        self.marker_path = self.root / MANAGED_MARKER_NAME
        self.lock_path = self.root / MANAGED_LOCK_NAME
        self.staging_root = self.root / "staging"
        self.completed_root = self.root / "completed"
        self.failed_root = self.root / "failed"
        self._lock_handle: IO[str] | None = None

    def start_server(self) -> None:
        """Claim ownership, lock for the lifetime, and empty stale artifacts."""

        if self._lock_handle is not None:
            raise RuntimeError("runtime root is already active in this process.")
        self._prepare_owned_root()
        handle = self._open_lock_file()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeRootLockedError(
                f"runtime root is already owned by another application server: "
                f"{self.root}; no artifacts were purged."
            ) from exc
        self._lock_handle = handle
        try:
            self._validate_owned_structure(validate_artifacts=True)
            self._purge_artifact_directories()
            self._create_artifact_directories()
        except BaseException:
            self._release_lock()
            raise

    def stop_server(self, *, keep_runtime: bool = False) -> None:
        """Clean temporary artifacts, warning on failure, then release the lock."""

        if self._lock_handle is None:
            return
        try:
            if not keep_runtime:
                try:
                    self._validate_owned_structure(validate_artifacts=True)
                    self._purge_artifact_directories()
                    self._create_artifact_directories()
                except BaseException as exc:
                    LOGGER.warning(
                        "failed to clean managed runtime artifacts at %s: %s",
                        self.root,
                        exc,
                    )
        finally:
            try:
                self._release_lock()
            except BaseException as exc:
                LOGGER.warning(
                    "failed to release the managed runtime lock at %s: %s",
                    self.root,
                    exc,
                )

    def initialize(self) -> None:
        """Initialize a safe empty/marked root without purging existing artifacts.

        Server code must use :meth:`start_server`, which also holds the lifetime
        lock and performs stale cleanup.  This method remains for isolated
        workspace consumers and tests.
        """

        self._prepare_owned_root()
        self._validate_owned_structure(validate_artifacts=True)
        self._create_artifact_directories()

    def allocate_staging(self, run_id: str) -> Path:
        validated = validate_run_id(run_id)
        destination = self.staging_root / validated
        destination.mkdir()
        return destination

    def promote(self, run_id: str) -> Path:
        validated = validate_run_id(run_id)
        source = self._run_path(self.staging_root, validated, must_exist=True)
        destination = self._run_path(
            self.completed_root, validated, must_exist=False
        )
        if destination.exists():
            raise FileExistsError(f"completed run already exists: {validated}")
        os.replace(source, destination)
        return destination

    def retain_failed(self, run_id: str) -> Path:
        validated = validate_run_id(run_id)
        source = self._run_path(self.staging_root, validated, must_exist=True)
        destination = self._run_path(self.failed_root, validated, must_exist=False)
        if destination.exists():
            raise FileExistsError(f"failed run already exists: {validated}")
        os.replace(source, destination)
        return destination

    def completed_run(self, run_id: str) -> Path:
        return self._run_path(
            self.completed_root, validate_run_id(run_id), must_exist=True
        )

    def safe_viewer_file(self, run_id: str, relative: str) -> Path:
        run_root = self.completed_run(run_id)
        viewer_root = run_root / "plant-layout-viewer"
        posix = PurePosixPath(relative)
        if (
            not relative
            or posix.is_absolute()
            or any(part in {"", ".", ".."} for part in posix.parts)
            or posix.suffix.lower() not in VIEWER_SUFFIX_ALLOWLIST
        ):
            raise WorkspaceSafetyError("viewer artifact path is not allowed.")
        candidate = viewer_root.joinpath(*posix.parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != viewer_root.parent
        ):
            raise WorkspaceSafetyError("viewer artifact symlinks are not allowed.")
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError("viewer artifact not found.") from exc
        resolved_viewer = viewer_root.resolve(strict=True)
        if not resolved.is_relative_to(resolved_viewer) or not resolved.is_file():
            raise WorkspaceSafetyError("viewer artifact escaped its run boundary.")
        return resolved

    def safe_scatter_file(self, run_id: str, relative: str) -> Path:
        """Resolve only declared scatter resources and reused vendored Three.js."""

        run_root = self.completed_run(run_id)
        mapped = SCATTER_VIEWER_FILES.get(relative)
        if mapped is None:
            raise WorkspaceSafetyError("scatter viewer artifact path is not allowed.")
        candidate = run_root.joinpath(*PurePosixPath(mapped).parts)
        if candidate.is_symlink() or any(
            parent.is_symlink()
            for parent in candidate.parents
            if parent != run_root.parent
        ):
            raise WorkspaceSafetyError(
                "scatter viewer artifact symlinks are not allowed."
            )
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FileNotFoundError("scatter viewer artifact not found.") from exc
        if not resolved.is_relative_to(run_root.resolve()) or not resolved.is_file():
            raise WorkspaceSafetyError(
                "scatter viewer artifact escaped its run boundary."
            )
        return resolved

    def _prepare_owned_root(self) -> None:
        if self._requested_root.is_symlink():
            raise WorkspaceSafetyError("runtime root must not be a symlink.")
        if self.root.exists():
            if not self.root.is_dir():
                raise WorkspaceSafetyError("runtime root must be a directory.")
        else:
            try:
                self.root.mkdir()
            except FileNotFoundError as exc:
                raise WorkspaceSafetyError(
                    "runtime root parent must already exist."
                ) from exc
        if self.root.is_symlink():
            raise WorkspaceSafetyError("runtime root must not be a symlink.")
        entries = tuple(self.root.iterdir())
        if not entries:
            self._create_marker()
        elif not self.marker_path.exists() or self.marker_path.is_symlink():
            raise WorkspaceSafetyError(
                "refusing to adopt a nonempty runtime directory without the valid "
                f"{MANAGED_MARKER_NAME} ownership marker."
            )
        self._validate_marker()

    def _create_marker(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.marker_path, flags, 0o600)
        except FileExistsError:
            self._validate_marker()
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(MANAGED_MARKER_CONTENT)

    def _validate_marker(self) -> None:
        if self.marker_path.is_symlink() or not self.marker_path.is_file():
            raise WorkspaceSafetyError("managed runtime ownership marker is invalid.")
        try:
            content = self.marker_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise WorkspaceSafetyError(
                "managed runtime ownership marker cannot be read."
            ) from exc
        if content != MANAGED_MARKER_CONTENT:
            raise WorkspaceSafetyError(
                "managed runtime ownership marker has an unexpected version or content."
            )

    def _open_lock_file(self) -> IO[str]:
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise WorkspaceSafetyError("managed runtime lock file is unsafe.") from exc
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            os.close(descriptor)
            raise WorkspaceSafetyError("managed runtime lock must be a regular file.")
        return os.fdopen(descriptor, "r+", encoding="utf-8")

    def _validate_owned_structure(self, *, validate_artifacts: bool) -> None:
        if self._requested_root.is_symlink() or self.root.is_symlink():
            raise WorkspaceSafetyError("runtime root must not be a symlink.")
        self._validate_marker()
        allowed = {
            MANAGED_MARKER_NAME,
            MANAGED_LOCK_NAME,
            *ARTIFACT_DIRECTORY_NAMES,
        }
        unexpected = sorted(
            path.name for path in self.root.iterdir() if path.name not in allowed
        )
        if unexpected:
            raise WorkspaceSafetyError(
                "managed runtime root contains unexpected entries: "
                + ", ".join(unexpected)
            )
        if self.lock_path.exists() and (
            self.lock_path.is_symlink() or not self.lock_path.is_file()
        ):
            raise WorkspaceSafetyError(
                "managed runtime lock infrastructure is invalid."
            )
        for directory in self._artifact_roots():
            if directory.is_symlink():
                raise WorkspaceSafetyError(
                    "managed artifact directories must not be symlinks."
                )
            if directory.exists() and not directory.is_dir():
                raise WorkspaceSafetyError("managed artifact paths must be directories.")
            if validate_artifacts and directory.exists():
                self._validate_artifact_tree(directory)

    def _validate_artifact_tree(self, directory: Path) -> None:
        for child in directory.iterdir():
            if child.is_symlink():
                raise WorkspaceSafetyError(
                    f"managed artifacts must not contain symlinks: {child}"
                )
            if not child.is_dir() or RUN_ID_PATTERN.fullmatch(child.name) is None:
                raise WorkspaceSafetyError(
                    f"managed artifact directory contains an unexpected entry: {child}"
                )
            self._validate_run_tree(child)

    def _validate_run_tree(self, directory: Path) -> None:
        if os.path.ismount(directory):
            raise WorkspaceSafetyError(
                f"managed artifacts must not contain mounts: {directory}"
            )
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise WorkspaceSafetyError(
                        f"managed artifacts must not contain symlinks: {entry.path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    self._validate_run_tree(Path(entry.path))
                elif not entry.is_file(follow_symlinks=False):
                    raise WorkspaceSafetyError(
                        "managed artifacts contain an unsupported filesystem "
                        f"entry: {entry.path}"
                    )

    def _purge_artifact_directories(self) -> None:
        if self._lock_handle is None:
            raise RuntimeError("runtime cleanup requires the exclusive server lock.")
        for directory in self._artifact_roots():
            if directory.exists():
                shutil.rmtree(directory)

    def _create_artifact_directories(self) -> None:
        for directory in self._artifact_roots():
            directory.mkdir(exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise WorkspaceSafetyError("managed artifact directories are invalid.")

    def _release_lock(self) -> None:
        handle, self._lock_handle = self._lock_handle, None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _run_path(self, parent: Path, run_id: str, *, must_exist: bool) -> Path:
        path = parent / run_id
        if path.is_symlink():
            raise WorkspaceSafetyError("run workspace symlinks are not allowed.")
        if must_exist:
            resolved = path.resolve(strict=True)
            if not resolved.is_dir() or not resolved.is_relative_to(parent.resolve()):
                raise WorkspaceSafetyError("run workspace escaped its managed root.")
            return resolved
        resolved = path.resolve()
        if not resolved.is_relative_to(parent.resolve()):
            raise WorkspaceSafetyError("run workspace escaped its managed root.")
        return resolved

    def _artifact_roots(self) -> tuple[Path, Path, Path]:
        return self.staging_root, self.completed_root, self.failed_root


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or RUN_ID_PATTERN.fullmatch(value) is None:
        raise WorkspaceSafetyError(
            "run ID must be 32 lowercase hexadecimal characters."
        )
    return value


def _path_has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False
