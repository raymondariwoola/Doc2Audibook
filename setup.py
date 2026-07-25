"""Build hook that bundles the built React client into the wheel.

All packaging metadata lives in ``pyproject.toml``; this shim exists only to copy
the compiled frontend (``web/dist``) into the ``web_api`` package as
``web_api/frontend`` at build time. That way an installed wheel's ``narrate-web``
serves the React UI instead of exposing an API with no page (risk R-012).

Development and source-tree runs are unaffected: ``web_api.main`` prefers this
bundled copy only when it exists and otherwise falls back to ``web/dist``.

Build the frontend first so there is something to bundle::

    npm --prefix web ci && npm --prefix web run build
    python -m build            # or: pip wheel .
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_REPO_ROOT = Path(__file__).parent.resolve()
_FRONTEND_SOURCE = _REPO_ROOT / "web" / "dist"
_FRONTEND_TARGET = _REPO_ROOT / "web_api" / "frontend"


def bundle_frontend(source: Path, target: Path) -> bool:
    """Copy a built frontend from ``source`` into ``target``.

    Returns True when assets were copied, False when ``source`` has no build yet
    (the wheel is then API-only — the launcher and README explain how to build).
    """
    if not (source / "index.html").is_file():
        return False
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return True


class BuildPyWithFrontend(build_py):
    """Copy web/dist into web_api/frontend before packages/data are collected."""

    def run(self) -> None:
        if bundle_frontend(_FRONTEND_SOURCE, _FRONTEND_TARGET):
            self.announce(
                f"bundled frontend: {_FRONTEND_SOURCE} -> {_FRONTEND_TARGET}", level=2
            )
        else:
            self.warn(
                f"{_FRONTEND_SOURCE}/index.html not found; the wheel will ship "
                "API-only. Build the client first: "
                "`npm --prefix web ci && npm --prefix web run build`."
            )
        super().run()


if __name__ == "__main__":
    setup(cmdclass={"build_py": BuildPyWithFrontend})
