"""CLI boundary for public playback and explicit trusted-local live mode."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

import uvicorn

from .public import create_public_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics",
        description=(
            "Run authenticated precomputed playback by default, or explicitly "
            "enable the trusted-local Proposed live application."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable the trusted-local UI and Proposed-only native execution.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help=(
            "In --live mode, use a dedicated empty or valid marked absolute "
            "runtime directory outside the repository. "
            "Defaults to <repository>/.fspm-optics-runtime. Artifacts are "
            "temporary and cleaned at startup and shutdown."
        ),
    )
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help=(
            "In --live mode, preserve staging, completed, and failed artifacts "
            "after shutdown; startup locking, validation, and stale cleanup "
            "still apply."
        ),
    )
    parser.add_argument(
        "--precomputed-root",
        type=Path,
        help=(
            "Read validated compact playback bundles from this server-configured "
            "root. Defaults to <repository>/precomputed; clients cannot override it."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8895)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.live and (
        arguments.runtime_root is not None or arguments.keep_runtime
    ):
        parser.error("--runtime-root and --keep-runtime require --live")

    if not arguments.live:
        app = create_public_app(precomputed_root=arguments.precomputed_root)
        print(
            "FSPM Optics mode: public authenticated precomputed playback",
            flush=True,
        )
        uvicorn.run(
            app,
            host=arguments.host,
            port=arguments.port,
            workers=1,
            access_log=False,
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get(
                "FORWARDED_ALLOW_IPS", "127.0.0.1"
            ),
            server_header=False,
        )
        return

    from fspm_optics.layout.mode import (
        PROPOSED_LINEAR_LAYOUT_ENV_VAR,
        PROPOSED_LAYOUT_MODE_ENV_VAR,
        resolve_proposed_layout_startup_environment,
    )

    from .app import create_app
    from .runtime_session import resolve_runtime_root

    proposed_layout_mode = resolve_proposed_layout_startup_environment(
        os.environ.get(PROPOSED_LAYOUT_MODE_ENV_VAR),
        os.environ.get(PROPOSED_LINEAR_LAYOUT_ENV_VAR),
    )
    resolved = resolve_runtime_root(
        arguments.runtime_root,
        _repository_root(),
    )
    print("FSPM Optics mode: trusted-local live", flush=True)
    print(f"FSPM Optics runtime root: {resolved.path}", flush=True)
    app = create_app(
        runtime_root=resolved.path,
        keep_runtime=arguments.keep_runtime,
        proposed_layout_mode=proposed_layout_mode,
        precomputed_root=arguments.precomputed_root,
    )
    try:
        uvicorn.run(app, host=arguments.host, port=arguments.port)
    finally:
        # Uvicorn normally drives the ASGI lifespan cleanup.  This idempotent
        # fallback also covers an exception escaping before/after that path.
        app.state.shutdown_runtime()


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".git").exists():
            return parent
    raise RuntimeError("unable to locate the fspm-optics repository root.")


if __name__ == "__main__":
    main()
