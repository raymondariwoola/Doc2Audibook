"""Resolve ffmpeg/ffprobe consistently for source and packaged runtimes.

Source installs keep the historical PATH lookup. A desktop shell can instead
set an explicit absolute executable through ``DOC2AUDIOBOOK_FFMPEG`` or
``DOC2AUDIOBOOK_FFPROBE``. Invalid explicit overrides fail instead of silently
selecting a different machine-wide binary.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_TOOL_ENV = {
    "ffmpeg": "DOC2AUDIOBOOK_FFMPEG",
    "ffprobe": "DOC2AUDIOBOOK_FFPROBE",
}


def media_tool(name: str) -> str | None:
    """Return the configured executable path, or ``None`` when unavailable."""
    try:
        env_name = _TOOL_ENV[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported media tool '{name}'.") from exc

    configured = os.environ.get(env_name)
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            raise RuntimeError(f"{env_name} must be an absolute path.")
        if not candidate.is_file():
            raise RuntimeError(f"{env_name} does not point to a file: {candidate}")
        return str(candidate.resolve())
    return shutil.which(name)


def require_media_tool(name: str) -> str:
    """Resolve a media tool or raise an actionable setup error."""
    resolved = media_tool(name)
    if resolved:
        return resolved
    env_name = _TOOL_ENV[name]
    raise RuntimeError(
        f"{name} was not found. Install it (see README), put it on PATH, or set "
        f"{env_name} to its absolute executable path."
    )


def media_tools_status() -> dict[str, bool]:
    """Return availability without letting an invalid override break health."""
    status: dict[str, bool] = {}
    for name in _TOOL_ENV:
        try:
            status[name] = media_tool(name) is not None
        except RuntimeError:
            status[name] = False
    return status
