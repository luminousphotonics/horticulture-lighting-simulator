"""Synchronous local execution for generic :class:`CommandSpec` objects."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import subprocess
import time

from fspm_optics.radiance.commands import CommandSpec, validate_command_paths


class LocalRunnerError(RuntimeError):
    """Raised when a command cannot be launched or its paths are invalid."""


class LocalRunnerTimeoutError(LocalRunnerError):
    """Raised when a local command exceeds its configured timeout."""


@dataclass(frozen=True, slots=True)
class RunnerResult:
    """Completed local command metadata."""

    command_label: str
    argv: tuple[str, ...]
    returncode: int
    stdout_path: Path | None
    stderr_text: str | None
    stderr_path: Path | None
    wall_time_s: float
    success: bool

    @property
    def failure_message(self) -> str | None:
        if self.success:
            return None
        detail = (self.stderr_text or "").strip()
        message = (
            f"{self.command_label or self.argv[0]} failed with return code "
            f"{self.returncode}."
        )
        return f"{message} {detail}" if detail else message


class LocalRunner:
    """Run one command synchronously on the local workstation."""

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        timeout = _validate_timeout(timeout_s)
        label = command.label or command.argv[0]
        try:
            validate_command_paths(command)
        except OSError as exc:
            raise LocalRunnerError(f"{label} path validation failed: {exc}") from exc

        resolved_stderr_path = Path(stderr_path) if stderr_path is not None else None
        if resolved_stderr_path is not None and not resolved_stderr_path.parent.is_dir():
            raise LocalRunnerError(
                f"{label} stderr directory not found: {resolved_stderr_path.parent}"
            )
        if (
            resolved_stderr_path is not None
            and command.stdout_path is not None
            and resolved_stderr_path == command.stdout_path
        ):
            raise LocalRunnerError("stdout_path and stderr_path must be different files.")

        environment = os.environ.copy()
        environment.update(command.env)
        stdin_handle = None
        stdout_handle = None
        stderr_handle = None
        started = time.perf_counter()
        try:
            if command.stdin_path is not None:
                stdin_handle = command.stdin_path.open("r", encoding="utf-8")
            if command.stdout_path is not None:
                stdout_handle = (
                    command.stdout_path.open("wb")
                    if command.stdout_mode == "binary"
                    else command.stdout_path.open("w", encoding="utf-8")
                )
            if resolved_stderr_path is not None:
                stderr_handle = resolved_stderr_path.open("w", encoding="utf-8")

            try:
                completed = subprocess.run(
                    command.argv,
                    cwd=command.cwd,
                    env=environment,
                    stdin=stdin_handle,
                    stdout=(
                        stdout_handle
                        if stdout_handle is not None
                        else subprocess.DEVNULL
                    ),
                    stderr=(stderr_handle if stderr_handle is not None else subprocess.PIPE),
                    text=True,
                    timeout=timeout,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired as exc:
                elapsed = time.perf_counter() - started
                raise LocalRunnerTimeoutError(
                    f"{label} timed out after {timeout:.6g}s "
                    f"(wall time {elapsed:.6g}s): {command.argv!r}"
                ) from exc
            except (FileNotFoundError, PermissionError, OSError) as exc:
                raise LocalRunnerError(
                    f"{label} could not start {command.argv[0]!r}: {exc}"
                ) from exc

            wall_time = time.perf_counter() - started
            stderr_text = (
                None
                if resolved_stderr_path is not None
                else str(completed.stderr or "")
            )
            return RunnerResult(
                command_label=label,
                argv=command.argv,
                returncode=int(completed.returncode),
                stdout_path=command.stdout_path,
                stderr_text=stderr_text,
                stderr_path=resolved_stderr_path,
                wall_time_s=wall_time,
                success=completed.returncode == 0,
            )
        except LocalRunnerError:
            raise
        except OSError as exc:
            raise LocalRunnerError(f"{label} stream setup failed: {exc}") from exc
        finally:
            for handle in (stdin_handle, stdout_handle, stderr_handle):
                if handle is not None:
                    handle.close()


def _validate_timeout(timeout_s: float | None) -> float | None:
    if timeout_s is None:
        return None
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int | float):
        raise ValueError("timeout_s must be a positive finite number or None.")
    timeout = float(timeout_s)
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("timeout_s must be a positive finite number or None.")
    return timeout
