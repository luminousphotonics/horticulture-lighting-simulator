"""Local-first, non-executing Radiance command specifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Sequence

LOCAL_DEFAULT_NTHREADS = 6
StdoutMode = Literal["text", "binary"]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A reproducible command boundary with no execution behavior."""

    argv: tuple[str, ...]
    stdin_path: Path | None = None
    stdout_path: Path | None = None
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    label: str = ""
    stdout_mode: StdoutMode = "text"

    def __post_init__(self) -> None:
        argv = tuple(str(token) for token in self.argv)
        if not argv or any(not token for token in argv):
            raise ValueError("argv must contain non-empty command tokens.")
        environment = {str(key): str(value) for key, value in self.env.items()}
        if any(not key for key in environment):
            raise ValueError("env keys must be non-empty strings.")
        if self.stdout_mode not in ("text", "binary"):
            raise ValueError("stdout_mode must be 'text' or 'binary'.")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "stdin_path", _optional_path(self.stdin_path))
        object.__setattr__(self, "stdout_path", _optional_path(self.stdout_path))
        object.__setattr__(self, "cwd", _optional_path(self.cwd))
        object.__setattr__(self, "env", environment)
        object.__setattr__(self, "label", str(self.label))


def _optional_path(value: str | Path | None) -> Path | None:
    return None if value is None else Path(value)


def require_input_file(path: str | Path, *, label: str) -> Path:
    """Return a normalized existing file or raise a contextual error."""

    candidate = Path(path).expanduser()
    if not candidate.is_file():
        raise FileNotFoundError(f"{label} not found: {candidate}")
    return candidate


def require_working_directory(path: str | Path, *, label: str = "Working directory") -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_dir():
        raise NotADirectoryError(f"{label} not found or not a directory: {candidate}")
    return candidate


def validate_command_paths(command: CommandSpec) -> CommandSpec:
    """Validate local paths without locating or running the executable."""

    if command.stdin_path is not None:
        require_input_file(command.stdin_path, label=f"{command.label or 'Command'} stdin")
    if command.cwd is not None:
        require_working_directory(command.cwd)
    if command.stdout_path is not None:
        parent = command.stdout_path.parent
        if not parent.is_dir():
            raise NotADirectoryError(
                f"{command.label or 'Command'} stdout directory not found: {parent}"
            )
    return command


def build_oconv_argv(
    source_files: Sequence[str | Path],
    *,
    oconv_bin: str | Path = "oconv",
) -> tuple[str, ...]:
    """Build a full-source frozen-scene compilation argv."""

    executable = str(oconv_bin)
    sources = tuple(str(path) for path in source_files)
    if not executable:
        raise ValueError("oconv_bin must be non-empty.")
    if not sources or any(not source for source in sources):
        raise ValueError("At least one non-empty scene source path is required.")
    return (executable, "-f", *sources)


def build_oconv_command(
    source_files: Sequence[str | Path],
    *,
    output_octree: str | Path,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    oconv_bin: str | Path = "oconv",
    label: str = "compile_scene",
) -> CommandSpec:
    return CommandSpec(
        argv=build_oconv_argv(source_files, oconv_bin=oconv_bin),
        stdout_path=Path(output_octree),
        stdout_mode="binary",
        cwd=_optional_path(cwd),
        env=dict(env or {}),
        label=label,
    )


def build_baseline_rtrace_command(
    *,
    octree: str | Path,
    receiver_input: str | Path,
    rgb_output: str | Path,
    options: Sequence[str] = (),
    nthreads: int = LOCAL_DEFAULT_NTHREADS,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    rtrace_bin: str | Path = "rtrace",
) -> CommandSpec:
    """Describe baseline scalar irradiance tracing without executing it."""

    from fspm_optics.transport.scalar_ppfd import build_baseline_rtrace_argv

    return CommandSpec(
        argv=tuple(
            build_baseline_rtrace_argv(
                octree=octree,
                options=options,
                nthreads=nthreads,
                rtrace_bin=rtrace_bin,
            )
        ),
        stdin_path=Path(receiver_input),
        stdout_path=Path(rgb_output),
        cwd=_optional_path(cwd),
        env=dict(env or {}),
        label="baseline_scalar_ppfd_rtrace",
    )


def build_plant_receiver_rtrace_command(
    *,
    octree: str | Path,
    receiver_input: str | Path,
    rgb_output: str | Path,
    options: Sequence[str] = (),
    nthreads: int = LOCAL_DEFAULT_NTHREADS,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    rtrace_bin: str | Path = "rtrace",
) -> CommandSpec:
    """Describe plant receiver tracing using the scalar irradiance flags."""

    baseline = build_baseline_rtrace_command(
        octree=octree,
        receiver_input=receiver_input,
        rgb_output=rgb_output,
        options=options,
        nthreads=nthreads,
        cwd=cwd,
        env=env,
        rtrace_bin=rtrace_bin,
    )
    return CommandSpec(
        argv=baseline.argv,
        stdin_path=baseline.stdin_path,
        stdout_path=baseline.stdout_path,
        cwd=baseline.cwd,
        env=baseline.env,
        label="plant_receiver_rtrace",
    )
