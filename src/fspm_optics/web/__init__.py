"""ASGI application factories without eager live-worker imports."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "create_public_app":
        from .public import create_public_app

        return create_public_app
    raise AttributeError(name)


__all__ = ["create_app", "create_public_app"]
