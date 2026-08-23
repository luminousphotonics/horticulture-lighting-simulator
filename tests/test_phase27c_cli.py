from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from fspm_optics.runtime_paths import (
    DEFAULT_RUNTIME_DIRECTORY,
    MANAGED_MARKER_CONTENT,
    MANAGED_MARKER_NAME,
    is_default_managed_runtime_descendant,
)
from fspm_optics.web import runtime_session
from fspm_optics.web.runtime_session import resolve_runtime_root
from fspm_optics.web.workspaces import (
    RuntimeRootLockedError,
    RuntimeWorkspaces,
    WorkspaceSafetyError,
)
from fspm_optics.layout.mode import (
    PROPOSED_LAYOUT_MODE_ENV_VAR,
    PROPOSED_LINEAR_LAYOUT_ENV_VAR,
)


def test_both_console_entries_use_the_same_main_function() -> None:
    payload = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = payload["project"]["scripts"]

    assert scripts["fspm-optics"] == "fspm_optics.web.__main__:main"
    assert scripts["fspm-optics-web"] == "fspm_optics.web.__main__:main"


def test_runtime_arguments_are_optional_and_keep_is_opt_in() -> None:
    from fspm_optics.web.__main__ import build_parser

    arguments = build_parser().parse_args([])
    assert arguments.live is False
    assert arguments.runtime_root is None
    assert arguments.keep_runtime is False
    assert arguments.host == "127.0.0.1"
    assert arguments.port == 8895

    kept = build_parser().parse_args(["--keep-runtime"])
    assert kept.keep_runtime is True

    assert build_parser().parse_args(["--live"]).live is True


@pytest.mark.parametrize(
    ("selector", "linear_flag", "expected"),
    (
        ("legacy", None, "legacy"),
        (None, "1", "linear"),
        (None, None, "standalone_modules"),
    ),
)
def test_main_reads_proposed_layout_environment_once_and_injects_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str | None,
    linear_flag: str | None,
    expected: str,
) -> None:
    main_module = importlib.import_module("fspm_optics.web.__main__")
    app_module = importlib.import_module("fspm_optics.web.app")
    captured: dict[str, object] = {}
    fake_app = SimpleNamespace(
        state=SimpleNamespace(shutdown_runtime=lambda: captured.setdefault("shutdown", True))
    )
    if selector is None:
        monkeypatch.delenv(PROPOSED_LAYOUT_MODE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(PROPOSED_LAYOUT_MODE_ENV_VAR, selector)
    if linear_flag is None:
        monkeypatch.delenv(PROPOSED_LINEAR_LAYOUT_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(PROPOSED_LINEAR_LAYOUT_ENV_VAR, linear_flag)
    monkeypatch.setattr(
        runtime_session,
        "resolve_runtime_root",
        lambda *_args: SimpleNamespace(path=tmp_path / "runtime"),
    )
    monkeypatch.setattr(
        app_module,
        "create_app",
        lambda **kwargs: captured.update(kwargs) or fake_app,
    )
    monkeypatch.setattr(
        main_module.uvicorn,
        "run",
        lambda app, **kwargs: captured.update({"app": app, **kwargs}),
    )

    main_module.main(["--live"])

    assert captured["proposed_layout_mode"].value == expected  # type: ignore[union-attr]
    assert captured["shutdown"] is True


def test_default_is_exact_repository_root_and_ignores_xdg_and_temp(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    xdg = tmp_path / "xdg-runtime"
    xdg.mkdir()
    fallback = tmp_path / "fallback"
    fallback.mkdir()

    resolved = resolve_runtime_root(
        None,
        repository,
        environ={"XDG_RUNTIME_DIR": str(xdg)},
        fallback_directory=fallback,
    )

    assert resolved.path == (repository / DEFAULT_RUNTIME_DIRECTORY).resolve()
    assert resolved.automatic_session is True
    assert not resolved.path.exists(), "resolution must remain side-effect free"
    assert tuple(xdg.iterdir()) == ()
    assert tuple(fallback.iterdir()) == ()

    workspaces = RuntimeWorkspaces(resolved.path, repository)
    workspaces.start_server()
    try:
        assert resolved.path.is_dir(), "application startup creates the default"
    finally:
        workspaces.stop_server()


def test_default_runtime_directory_is_gitignored() -> None:
    repository = Path(__file__).parents[1]
    ignored = (repository / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"/{DEFAULT_RUNTIME_DIRECTORY}/" in ignored


def test_only_managed_artifact_descendants_get_repository_path_exception(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    workspaces = RuntimeWorkspaces(
        repository / DEFAULT_RUNTIME_DIRECTORY,
        repository,
    )
    workspaces.start_server()
    try:
        run = workspaces.staging_root / ("e" * 32)
        run.mkdir()
        assert is_default_managed_runtime_descendant(run / "native", repository)
        assert not is_default_managed_runtime_descendant(
            repository / "arbitrary" / "native", repository
        )
        assert not is_default_managed_runtime_descendant(
            workspaces.root / "unexpected", repository
        )
    finally:
        workspaces.stop_server()


def test_import_does_not_write_to_xdg_or_create_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    xdg = tmp_path / "xdg-runtime"
    xdg.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))
    monkeypatch.chdir(repository)

    importlib.reload(runtime_session)

    assert tuple(xdg.iterdir()) == ()
    assert not (repository / DEFAULT_RUNTIME_DIRECTORY).exists()


def test_explicit_root_is_supported_and_created_only_at_server_start(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    explicit = tmp_path / "alternate-disk" / "runtime"
    explicit.parent.mkdir()

    resolved = resolve_runtime_root(explicit, repository)
    assert resolved.path == explicit.resolve()
    assert resolved.automatic_session is False
    assert not explicit.exists()

    workspaces = RuntimeWorkspaces(resolved.path, repository)
    workspaces.start_server()
    try:
        assert explicit.is_dir()
        assert (explicit / MANAGED_MARKER_NAME).read_text(encoding="utf-8") == (
            MANAGED_MARKER_CONTENT
        )
    finally:
        workspaces.stop_server()


def test_stale_artifacts_are_purged_only_after_lock_acquisition(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    previous = RuntimeWorkspaces(runtime, repository)
    previous.start_server()
    stale_run = "a" * 32
    (previous.completed_root / stale_run).mkdir()
    (previous.completed_root / stale_run / "artifact.txt").write_text(
        "stale", encoding="utf-8"
    )
    previous.stop_server(keep_runtime=True)

    current = RuntimeWorkspaces(runtime, repository)
    current.start_server()
    try:
        assert tuple(current.staging_root.iterdir()) == ()
        assert tuple(current.completed_root.iterdir()) == ()
        assert tuple(current.failed_root.iterdir()) == ()
    finally:
        current.stop_server()


def test_artifacts_live_until_shutdown_and_keep_runtime_preserves_them(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    run_id = "b" * 32

    temporary = RuntimeWorkspaces(runtime, repository)
    temporary.start_server()
    (temporary.completed_root / run_id).mkdir()
    artifact = temporary.completed_root / run_id / "artifact.txt"
    artifact.write_text("available", encoding="utf-8")
    assert artifact.read_text(encoding="utf-8") == "available"
    temporary.stop_server()
    assert tuple(temporary.completed_root.iterdir()) == ()

    kept = RuntimeWorkspaces(runtime, repository)
    kept.start_server()
    (kept.failed_root / run_id).mkdir()
    kept_artifact = kept.failed_root / run_id / "failure.json"
    kept_artifact.write_text("{}", encoding="utf-8")
    kept.stop_server(keep_runtime=True)
    assert kept_artifact.is_file()


def test_second_live_server_cannot_purge_first_server_artifacts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    owner = RuntimeWorkspaces(runtime, repository)
    owner.start_server()
    run = owner.completed_root / ("c" * 32)
    run.mkdir()
    artifact = run / "artifact.txt"
    artifact.write_text("owned", encoding="utf-8")

    contender = RuntimeWorkspaces(runtime, repository)
    try:
        with pytest.raises(RuntimeRootLockedError, match="another application server"):
            contender.start_server()
        assert artifact.read_text(encoding="utf-8") == "owned"
    finally:
        owner.stop_server()


def test_unsafe_roots_and_unmarked_nonempty_directories_fail_closed(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(WorkspaceSafetyError, match="never equal"):
        RuntimeWorkspaces(repository, repository)
    with pytest.raises(WorkspaceSafetyError, match="exact managed default"):
        RuntimeWorkspaces(repository / "arbitrary-runtime", repository)
    with pytest.raises(WorkspaceSafetyError, match="absolute"):
        RuntimeWorkspaces("relative-runtime", repository)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    sentinel = nonempty / "do-not-delete.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(WorkspaceSafetyError, match="refusing to adopt"):
        RuntimeWorkspaces(nonempty, repository).start_server()
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked-runtime"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(WorkspaceSafetyError, match="symlink"):
        RuntimeWorkspaces(linked, repository).start_server()


def test_unexpected_structure_and_symlinks_cannot_escape_cleanup(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "source.py"
    sentinel.write_text("repository-like source", encoding="utf-8")

    prepared = RuntimeWorkspaces(runtime, repository)
    prepared.start_server()
    run = prepared.completed_root / ("d" * 32)
    run.mkdir()
    (run / "escape").symlink_to(outside, target_is_directory=True)
    prepared.stop_server(keep_runtime=True)

    with pytest.raises(WorkspaceSafetyError, match="must not contain symlinks"):
        RuntimeWorkspaces(runtime, repository).start_server()
    assert sentinel.read_text(encoding="utf-8") == "repository-like source"
    assert (run / "escape").is_symlink()


def test_marked_root_with_unexpected_content_is_not_purged(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    prepared = RuntimeWorkspaces(runtime, repository)
    prepared.start_server()
    prepared.stop_server(keep_runtime=True)
    unexpected = runtime / "source-tree"
    unexpected.mkdir()
    sentinel = unexpected / "module.py"
    sentinel.write_text("do not purge", encoding="utf-8")

    with pytest.raises(WorkspaceSafetyError, match="unexpected entries"):
        RuntimeWorkspaces(runtime, repository).start_server()
    assert sentinel.read_text(encoding="utf-8") == "do not purge"


def test_symlinked_managed_artifact_directory_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("safe", encoding="utf-8")
    prepared = RuntimeWorkspaces(runtime, repository)
    prepared.start_server()
    prepared.stop_server(keep_runtime=True)
    prepared.staging_root.rmdir()
    prepared.staging_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceSafetyError, match="must not be symlinks"):
        RuntimeWorkspaces(runtime, repository).start_server()
    assert sentinel.read_text(encoding="utf-8") == "safe"


def test_cli_finally_cleans_after_an_exception_escaping_uvicorn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fspm_optics.web import __main__ as cli
    from fspm_optics.web import app as app_module

    session = tmp_path / "runtime"
    shutdown_calls: list[bool] = []
    app = SimpleNamespace(
        state=SimpleNamespace(
            shutdown_runtime=lambda: shutdown_calls.append(True),
        )
    )
    monkeypatch.setattr(
        runtime_session,
        "resolve_runtime_root",
        lambda _root, _repository: runtime_session.RuntimeRootResolution(
            session, True
        ),
    )
    monkeypatch.setattr(
        app_module,
        "create_app",
        lambda **_kwargs: app,
    )

    def fail_uvicorn(_app: object, *, host: str, port: int) -> None:
        raise RuntimeError(f"uvicorn failed at {host}:{port}")

    monkeypatch.setattr(cli.uvicorn, "run", fail_uvicorn)

    with pytest.raises(RuntimeError, match="uvicorn failed"):
        cli.main(["--live"])
    assert shutdown_calls == [True]
