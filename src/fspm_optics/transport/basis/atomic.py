"""Same-directory staged writes with atomic final replacement."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import numpy as np
from numpy.typing import NDArray


def stage_bytes(path: str | Path, data: bytes) -> Path:
    """Write and fsync bytes to a temporary sibling of the final path."""

    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.",
        suffix=".tmp",
        dir=final_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def stage_npy(path: str | Path, matrix: NDArray[np.float64]) -> Path:
    """Serialize a NumPy matrix to a temporary sibling and fsync it."""

    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.",
        suffix=".tmp",
        dir=final_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            np.save(handle, matrix, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def commit_staged(temporary_path: str | Path, final_path: str | Path) -> Path:
    """Atomically replace the final path with a fully staged sibling."""

    temporary = Path(temporary_path)
    final = Path(final_path)
    os.replace(temporary, final)
    return final


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    final = Path(path)
    temporary = stage_bytes(final, data)
    try:
        return commit_staged(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_save_npy(path: str | Path, matrix: NDArray[np.float64]) -> Path:
    final = Path(path)
    temporary = stage_npy(final, matrix)
    try:
        return commit_staged(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)
