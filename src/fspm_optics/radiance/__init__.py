"""Radiance command boundaries, local execution, and option helpers."""

from .commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_argv,
    build_oconv_command,
    build_plant_receiver_rtrace_command,
    require_input_file,
    require_working_directory,
    validate_command_paths,
)
from .executables import ExecutableResolutionError, resolve_executable
from .options import (
    radiance_option_value,
    radiance_options,
    replace_radiance_option_value,
)
from .runner import (
    LocalRunner,
    LocalRunnerError,
    LocalRunnerTimeoutError,
    RunnerResult,
)
from .versioning import (
    RadianceDiscoveryError,
    RadianceExecutableVersion,
    RadianceInstallation,
    discover_radiance_installation,
    probe_radiance_version,
)

__all__ = [
    "ExecutableResolutionError",
    "LOCAL_DEFAULT_NTHREADS",
    "CommandSpec",
    "LocalRunner",
    "LocalRunnerError",
    "LocalRunnerTimeoutError",
    "RunnerResult",
    "RadianceDiscoveryError",
    "RadianceExecutableVersion",
    "RadianceInstallation",
    "build_baseline_rtrace_command",
    "build_oconv_argv",
    "build_oconv_command",
    "build_plant_receiver_rtrace_command",
    "radiance_option_value",
    "radiance_options",
    "replace_radiance_option_value",
    "require_input_file",
    "require_working_directory",
    "resolve_executable",
    "discover_radiance_installation",
    "probe_radiance_version",
    "validate_command_paths",
]
