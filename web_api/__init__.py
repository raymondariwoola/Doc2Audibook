"""Local web and desktop-sidecar interfaces.

Package import stays lazy so a frozen desktop process can apply absolute
runtime paths before ``providers`` and the default FastAPI app are constructed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from .main import app, create_app

    return {"app": app, "create_app": create_app}[name]
