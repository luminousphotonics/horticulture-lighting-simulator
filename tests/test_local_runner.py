from __future__ import annotations

from pathlib import Path
import sys

import pytest

from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import (
    LocalRunner,
    LocalRunnerError,
    LocalRunnerTimeoutError,
)


def _python_command(
    code: str,
    *,
    stdout_path: Path | None = None,
    stdin_path: Path | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    label: str = "python_test",
    extra_args: tuple[str, ...] = (),
) -> CommandSpec:
    return CommandSpec(
        argv=(sys.executable, "-c", code, *extra_args),
        stdin_path=stdin_path,
        stdout_path=stdout_path,
        cwd=cwd,
        env=env or {},
        label=label,
    )


def test_stdout_path_is_written_and_result_is_complete(tmp_path: Path) -> None:
    output = tmp_path / "stdout.txt"
    command = _python_command("print('local runner')", stdout_path=output)
    result = LocalRunner().run(command)
    assert output.read_text(encoding="utf-8") == "local runner\n"
    assert result.command_label == "python_test"
    assert result.argv == command.argv
    assert result.returncode == 0
    assert result.stdout_path == output
    assert result.stderr_text == ""
    assert result.stderr_path is None
    assert result.wall_time_s >= 0.0
    assert result.success is True
    assert result.failure_message is None


def test_binary_stdout_path_preserves_non_text_bytes(tmp_path: Path) -> None:
    output = tmp_path / "stdout.bin"
    command = CommandSpec(
        argv=(
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(bytes((0, 255, 10, 128)))",
        ),
        stdout_path=output,
        stdout_mode="binary",
        label="python_binary_test",
    )

    result = LocalRunner().run(command)

    assert result.success is True
    assert output.read_bytes() == bytes((0, 255, 10, 128))


def test_stdin_path_is_read_from_file(tmp_path: Path) -> None:
    source = tmp_path / "stdin.txt"
    output = tmp_path / "stdout.txt"
    source.write_text("scalar ppfd\n", encoding="utf-8")
    command = _python_command(
        "import sys; sys.stdout.write(sys.stdin.read().upper())",
        stdin_path=source,
        stdout_path=output,
    )
    result = LocalRunner().run(command)
    assert result.success is True
    assert output.read_text(encoding="utf-8") == "SCALAR PPFD\n"


def test_cwd_is_applied(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    output = tmp_path / "cwd.txt"
    command = _python_command(
        "import os; print(os.getcwd())",
        stdout_path=output,
        cwd=work,
    )
    LocalRunner().run(command)
    assert output.read_text(encoding="utf-8").strip() == str(work)


def test_environment_overlays_current_environment(tmp_path: Path) -> None:
    output = tmp_path / "env.txt"
    command = _python_command(
        "import os; print(os.environ['FSPM_LOCAL_RUNNER_TEST']); print(bool(os.environ.get('PATH')))",
        stdout_path=output,
        env={"FSPM_LOCAL_RUNNER_TEST": "overlay-value"},
    )
    LocalRunner().run(command)
    assert output.read_text(encoding="utf-8").splitlines() == [
        "overlay-value",
        "True",
    ]


def test_nonzero_returncode_and_stderr_are_captured() -> None:
    command = _python_command(
        "import sys; print('intentional failure', file=sys.stderr); sys.exit(7)",
        label="nonzero_test",
    )
    result = LocalRunner().run(command)
    assert result.returncode == 7
    assert result.success is False
    assert result.stderr_text == "intentional failure\n"
    assert result.stderr_path is None
    assert result.failure_message == (
        "nonzero_test failed with return code 7. intentional failure"
    )


def test_stderr_can_be_written_to_a_file(tmp_path: Path) -> None:
    stderr_path = tmp_path / "stderr.txt"
    command = _python_command("import sys; print('warning', file=sys.stderr)")
    result = LocalRunner().run(command, stderr_path=stderr_path)
    assert result.success is True
    assert result.stderr_text is None
    assert result.stderr_path == stderr_path
    assert stderr_path.read_text(encoding="utf-8") == "warning\n"


def test_timeout_raises_clear_runner_error() -> None:
    command = _python_command("import time; time.sleep(1)", label="timeout_test")
    with pytest.raises(LocalRunnerTimeoutError, match="timeout_test timed out"):
        LocalRunner().run(command, timeout_s=0.05)


def test_shell_metacharacters_are_not_interpreted(tmp_path: Path) -> None:
    marker = tmp_path / "must_not_exist"
    output = tmp_path / "literal.txt"
    literal = f"value; touch {marker}"
    command = _python_command(
        "import sys; print(sys.argv[1])",
        stdout_path=output,
        extra_args=(literal,),
    )
    result = LocalRunner().run(command)
    assert result.success is True
    assert output.read_text(encoding="utf-8").strip() == literal
    assert not marker.exists()


def test_missing_executable_has_clear_error() -> None:
    command = CommandSpec(argv=("definitely-not-a-real-local-command",), label="missing")
    with pytest.raises(LocalRunnerError, match="missing could not start"):
        LocalRunner().run(command)
