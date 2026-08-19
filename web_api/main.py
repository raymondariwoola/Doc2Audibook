"""FastAPI backend for the Phase 3 local web application.

This module deliberately stays on the interface side of the project boundary:
it translates HTTP requests into calls to ``narrate`` and ``providers`` without
changing either reusable layer. Jobs live in memory while their uploaded
documents, resumable chunks, previews, and finished audio stay in gitignored
machine-local directories.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

import narrate
import providers
from media_tools import media_tool, media_tools_status


SERVICE_VERSION = "0.1.0"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_ROOT = REPO_ROOT / "outputs" / "web_jobs"
DEFAULT_PREVIEW_ROOT = REPO_ROOT / ".narrate_cache" / "web_previews"


def _resolve_frontend_dist(bundled: Path, source: Path) -> Path:
    """Serve the React client from the copy bundled into an installed wheel
    (``web_api/frontend``) when present, else the source-tree ``web/dist`` build
    used during development. This keeps ``narrate-web`` from a wheel from exposing
    the API without its UI (R-012)."""
    if (bundled / "index.html").is_file():
        return bundled
    return source


# The build hook (setup.py) copies web/dist into this package as web_api/frontend.
_BUNDLED_FRONTEND = Path(__file__).resolve().parent / "frontend"
DEFAULT_FRONTEND_DIST = _resolve_frontend_dist(
    _BUNDLED_FRONTEND, REPO_ROOT / "web" / "dist"
)
DEFAULT_PREVIEW_TEXT = (
    "Hello — this is a preview of how your audiobook will sound. "
    "Choose the voice that feels right for your story."
)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_RULE_JSON_CHARS = 64 * 1024
MAX_RULES = 250
MAX_RULE_SOURCE_CHARS = 500
MAX_RULE_TARGET_CHARS = 2_000
DESKTOP_TOKEN_ENV = "DOC2AUDIOBOOK_DESKTOP_TOKEN"
DESKTOP_SESSION_COOKIE = "doc2audiobook_desktop_session"
DESKTOP_TOKEN_HEADER = "X-Doc2Audiobook-Desktop-Token"
# Bound on jobs that are queued or running at once. Generation is already
# serialized (R-004), so this caps the *waiting* set a burst can create; new
# submissions past it are rejected with 429 (deduped resubmissions are exempt).
MAX_ACTIVE_JOBS = 32
# Idle interval after which the SSE stream emits a comment heartbeat so long
# proxy/browser connections are not dropped between progress updates (R-009).
SSE_HEARTBEAT_SECONDS = 15.0
_SSE_POLL_SECONDS = 0.15
_SSE_HEARTBEAT_FRAME = ": keep-alive\n\n"
TERMINAL_STATUSES = frozenset({"completed", "failed"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
JobStatus = Literal["queued", "running", "completed", "failed"]
_JOB_METADATA_NAME = "job.json"
_JOB_PATH_FIELDS = ("input_path", "output_path", "work_dir")
logger = logging.getLogger(__name__)

ALLOWED_BROWSER_ORIGINS = frozenset(
    {
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://[::1]:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
)
STATE_CHANGING_PATHS = frozenset({"/api/preview", "/preview", "/api/jobs", "/jobs"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rate(value: int) -> str:
    return f"{value:+d}%"


def _pitch(value: int) -> str:
    return f"{value:+d}Hz"


def _synthesis_failure_detail(exc: Exception, *, action: str) -> str:
    """Turn a synthesis exception into a sanitized, actionable client message.

    Local providers (Piper/Kokoro) transcode to MP3 with ffmpeg, so a missing
    ffmpeg is the most common non-obvious failure — surface it specifically
    instead of a generic error. ``action`` is a short verb phrase, e.g.
    "create the preview" or "generate the audiobook".
    """
    if "ffmpeg" in str(exc).lower():
        try:
            ffmpeg_available = media_tool("ffmpeg") is not None
        except RuntimeError:
            ffmpeg_available = False
        if not ffmpeg_available:
            return (
                "This voice needs ffmpeg to produce audio, but ffmpeg was not found "
                "on PATH. Install it (see the README), then restart the app from a "
                "terminal where `ffmpeg -version` works."
            )
        return f"Audio encoding failed while trying to {action}. Check the ffmpeg install and try again."
    return f"Could not {action}. Check that the selected provider and voice are available, then try again."


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")
    return cleaned[:80] or "audiobook"


def _origin_allowed(request: Request, origin: str | None) -> bool:
    """Allow non-browser clients, known dev origins, and true local same-origin."""
    if origin is None:
        return True
    normalized = origin.casefold()
    if normalized in ALLOWED_BROWSER_ORIGINS:
        return True

    # Keep --port useful without opening cross-origin access: an alternate
    # loopback port is accepted only when Origin exactly matches the Host.
    host = request.headers.get("host", "").casefold()
    for prefix in ("http://", "https://"):
        if normalized == f"{prefix}{host}" and (
            host == "localhost"
            or host.startswith("localhost:")
            or host == "127.0.0.1"
            or host.startswith("127.0.0.1:")
            or host == "[::1]"
            or host.startswith("[::1]:")
        ):
            return True
    return False


def _is_api_request_path(path: str) -> bool:
    """Recognize canonical and compatibility API routes for desktop auth."""
    if path == "/api" or path.startswith("/api/"):
        return True
    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in ("/health", "/providers", "/preview", "/jobs")
    )


def _provider_or_404(provider_id: str) -> providers.TTSProvider:
    try:
        return providers.get_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_available(provider_id: str) -> providers.TTSProvider:
    provider = _provider_or_404(provider_id)
    if not provider.is_available():
        caps = provider.capabilities()
        reason = "requires an API key" if caps.needs_api_key else "is unavailable"
        raise HTTPException(
            status_code=409,
            detail=f"Provider '{provider_id}' {reason} on this machine.",
        )
    return provider


class PreviewRequest(BaseModel):
    provider: str = providers.DEFAULT_PROVIDER_ID
    voice: str = Field(min_length=1, max_length=200)
    rate: int = Field(default=-4, ge=-100, le=100)
    pitch: int = Field(default=0, ge=-100, le=100)
    text: str = Field(default=DEFAULT_PREVIEW_TEXT, min_length=1, max_length=500)


@dataclass
class JobRecord:
    id: str
    filename: str
    settings: dict[str, Any]
    input_path: Path
    output_path: Path
    work_dir: Path
    status: JobStatus = "queued"
    stage: str = "queued"
    progress: float = 0.0
    message: str = "Waiting to start"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    result: dict[str, Any] | None = None
    error: str | None = None
    version: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "filename": self.filename,
            "settings": self.settings,
            "result": self.result,
            "error": self.error,
        }

    def to_disk(self) -> dict[str, Any]:
        """JSON-safe snapshot of the full record, including internal paths."""
        data = asdict(self)
        for key in _JOB_PATH_FIELDS:
            data[key] = str(data[key])
        return data

    @classmethod
    def from_disk(cls, data: dict[str, Any]) -> "JobRecord":
        """Rebuild a record from persisted metadata, tolerating schema drift."""
        known = {f.name for f in dataclass_fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        for key in _JOB_PATH_FIELDS:
            if key in values:
                values[key] = Path(values[key])
        return cls(**values)


class JobStore:
    """Small process-local registry with thread-safe callback updates."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.jobs_root = self.root / "jobs"
        self.uploads_root = self.root / "uploads"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.uploads_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()
        self._load_persisted()

    # -- persistence -------------------------------------------------------

    def _write(self, job: JobRecord) -> None:
        """Atomically persist one job's metadata under its job directory."""
        job_dir = self.jobs_root / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        temporary = job_dir / f"{_JOB_METADATA_NAME}.tmp"
        temporary.write_text(
            json.dumps(job.to_disk(), ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(job_dir / _JOB_METADATA_NAME)

    def persist(self, job: JobRecord) -> None:
        with self._lock:
            self._write(job)

    def _load_persisted(self) -> None:
        """Reload jobs from disk and recover any interrupted by a restart.

        Jobs still marked queued/running had no in-memory task to resume, so
        they are reconciled to a clear failed state. Because renders are
        content-addressed and completed chunks are retained, resubmitting the
        same document and settings picks up where the interrupted job stopped.
        """
        for meta in sorted(self.jobs_root.glob(f"*/{_JOB_METADATA_NAME}")):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                job = JobRecord.from_disk(data)
            except Exception:  # noqa: BLE001 - skip unreadable/foreign metadata
                logger.warning("Ignoring unreadable job metadata at %s", meta)
                continue
            if job.status in ACTIVE_STATUSES:
                job.status = "failed"
                job.stage = "failed"
                job.message = "Interrupted by a server restart"
                job.error = (
                    "The server restarted before this job finished. Resubmit the "
                    "same document and settings to resume; completed chunks were kept."
                )
                job.updated_at = _now()
                job.version += 1
                self._jobs[job.id] = job
                self._write(job)
            else:
                self._jobs[job.id] = job

    # -- registry ----------------------------------------------------------

    def reserve(
        self, job: JobRecord, *, max_active: int | None = None
    ) -> tuple[JobRecord | None, bool]:
        """Return an existing equivalent job, or reserve the new one.

        Returns ``(None, False)`` when reserving a genuinely new job would
        exceed ``max_active`` in-flight jobs; deduped resubmissions are exempt.
        """
        with self._lock:
            existing = self._jobs.get(job.id)
            if existing and (
                existing.status in ACTIVE_STATUSES
                or (existing.status == "completed" and existing.output_path.is_file())
            ):
                return existing, False
            if max_active is not None and self._active_count() >= max_active:
                return None, False
            self._jobs[job.id] = job
            return job, True

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> tuple[dict[str, Any], int] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return (job.public(), job.version) if job else None

    def _active_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status in ACTIVE_STATUSES)

    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            ordered = sorted(
                self._jobs.values(), key=lambda job: job.created_at, reverse=True
            )
            return [job.public() for job in ordered]

    def update(self, job_id: str, **changes: Any) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = _now()
            job.version += 1
            self._write(job)
            return job


class SPAStaticFiles(StaticFiles):
    """Static assets with client-side routing, excluding the API namespace."""

    async def get_response(self, path: str, scope: dict[str, Any]):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or self._is_api_path(path, scope):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not self._is_api_path(path, scope):
            return await super().get_response("index.html", scope)
        return response

    @staticmethod
    def _is_api_path(path: str, scope: dict[str, Any]) -> bool:
        candidates = (path, str(scope.get("path", "")))
        return any(
            (clean := candidate.lstrip("/")) == "api" or clean.startswith("api/")
            for candidate in candidates
        )


class IPv6CompatibleTrustedHostMiddleware(TrustedHostMiddleware):
    """Retain Starlette's host checks while handling bracketed IPv6 loopback.

    Starlette 1.3 splits Host at the first colon, so ``[::1]`` otherwise becomes
    ``[`` and is rejected. Only the canonical loopback spelling receives this
    narrow compatibility path; every other host uses the upstream middleware.
    """

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in {"http", "websocket"}:
            host = Headers(scope=scope).get("host", "").casefold()
            match = re.fullmatch(r"\[::1\](?::(\d{1,5}))?", host)
            if match:
                port = int(match.group(1)) if match.group(1) else None
                if port is None or 1 <= port <= 65535:
                    await self.app(scope, receive, send)
                    return
        await super().__call__(scope, receive, send)


def _repo_relative(path: Path | str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.resolve()


def _parse_rules(raw: str) -> list[tuple[str, str]]:
    if len(raw) > MAX_RULE_JSON_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Rules JSON cannot exceed {MAX_RULE_JSON_CHARS} characters.",
        )
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Rules must be valid JSON: {exc.msg}.",
        ) from exc

    pairs: list[tuple[Any, Any]]
    if isinstance(data, dict):
        pairs = list(data.items())
    elif isinstance(data, list):
        pairs = []
        for index, item in enumerate(data):
            if not isinstance(item, dict) or "from" not in item or "to" not in item:
                raise HTTPException(
                    status_code=422,
                    detail=f"Rule {index + 1} must contain 'from' and 'to'.",
                )
            pairs.append((item["from"], item["to"]))
    else:
        raise HTTPException(
            status_code=422,
            detail="Rules must be an object or a list of {'from','to'} objects.",
        )

    if len(pairs) > MAX_RULES:
        raise HTTPException(
            status_code=422,
            detail=f"A job can contain at most {MAX_RULES} replacement rules.",
        )

    rules: list[tuple[str, str]] = []
    for index, (source, target) in enumerate(pairs, start=1):
        if not isinstance(source, str) or not isinstance(target, str):
            raise HTTPException(
                status_code=422,
                detail=f"Rule {index} values must be strings.",
            )
        if not source:
            raise HTTPException(
                status_code=422,
                detail=f"Rule {index} cannot have an empty 'from' value.",
            )
        if len(source) > MAX_RULE_SOURCE_CHARS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Rule {index} 'from' value cannot exceed "
                    f"{MAX_RULE_SOURCE_CHARS} characters."
                ),
            )
        if len(target) > MAX_RULE_TARGET_CHARS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Rule {index} 'to' value cannot exceed "
                    f"{MAX_RULE_TARGET_CHARS} characters."
                ),
            )
        rules.append((source, target))
    return rules


async def _save_upload(upload: UploadFile, destination: Path) -> str:
    """Stream an upload to disk, returning its SHA-256 digest."""
    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Document exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                    )
                digest.update(chunk)
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return digest.hexdigest()


async def _save_desktop_document(source: Path, destination: Path) -> str:
    """Copy a native-picker selection into managed storage and hash it."""
    try:
        source = source.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(
            status_code=422, detail="The selected document is unavailable."
        ) from exc
    if not source.is_file():
        raise HTTPException(status_code=422, detail="The selected document is not a file.")
    if source.stat().st_size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Document exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )

    def copy_and_hash() -> str:
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as incoming, destination.open("wb") as output:
                while chunk := incoming.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "Document exceeds the "
                                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
                            ),
                        )
                    digest.update(chunk)
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return digest.hexdigest()

    return await asyncio.to_thread(copy_and_hash)


def _render_identity(
    content_hash: str,
    suffix: str,
    provider: providers.TTSProvider,
    settings: dict[str, Any],
) -> str:
    payload = {
        "schema": 1,
        "content_sha256": content_hash,
        "document_suffix": suffix,
        "provider_identity": provider.cache_identity(),
        **settings,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _run_job(app: FastAPI, job_id: str) -> None:
    store: JobStore = app.state.job_store
    job = store.get(job_id)
    if job is None:
        return

    def on_stage(stage: str, info: dict[str, Any]) -> None:
        if stage == "extracted":
            chunks = int(info.get("chunks", 0))
            store.update(
                job_id,
                stage="synthesizing",
                progress=0.08,
                message=f"Prepared {chunks} audio chunk{'s' if chunks != 1 else ''}",
            )
        elif stage == "assembling":
            store.update(
                job_id,
                stage="assembling",
                progress=0.95,
                message="Assembling the finished audiobook",
            )
        elif stage == "done":
            store.update(
                job_id,
                stage="finalizing",
                progress=0.99,
                message="Finalizing output",
            )

    def on_chunk(index: int, total: int, chars: int, status: str) -> None:
        del chars
        fraction = index / max(total, 1)
        store.update(
            job_id,
            stage="synthesizing",
            progress=round(0.08 + 0.85 * fraction, 4),
            message=f"Synthesizing chunk {index} of {total} ({status})",
        )

    settings = job.settings
    rules = [
        (str(item["from"]), str(item["to"]))
        for item in settings.get("rules", [])
    ]
    try:
        # Provider instances (notably the local Piper/Kokoro engines) are
        # process singletons and are not guaranteed to be thread-safe. The
        # first web queue therefore serializes generation and previews.
        async with app.state.generation_lock:
            store.update(
                job_id,
                status="running",
                stage="extracting",
                progress=0.02,
                message="Extracting and preparing the document",
                error=None,
            )
            result = await narrate.build_audiobook_async(
                job.input_path,
                job.output_path,
                voice=settings["voice"],
                rate=_rate(settings["rate"]),
                pitch=_pitch(settings["pitch"]),
                fmt=settings["output_format"],
                provider=settings["provider"],
                user_rules=rules,
                work_dir=job.work_dir,
                keep_chunks=True,
                max_chars=settings["max_chars"],
                on_stage=on_stage,
                on_chunk=on_chunk,
            )
        public_result = {
            "filename": job.output_path.name,
            "format": result.fmt,
            "size": result.size,
            "duration": result.duration,
            "chunks": result.n_chunks,
            "total_chars": result.total_chars,
            "download_url": f"/api/jobs/{job_id}/output",
        }
        store.update(
            job_id,
            status="completed",
            stage="completed",
            progress=1.0,
            message="Audiobook ready",
            result=public_result,
            error=None,
        )
    except asyncio.CancelledError:
        store.update(
            job_id,
            status="failed",
            stage="failed",
            message="Generation was interrupted",
            error="The server stopped before this job completed.",
        )
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as sanitized job state
        logger.exception("Audiobook job %s failed", job_id)
        detail = _synthesis_failure_detail(exc, action="generate the audiobook")
        store.update(
            job_id,
            status="failed",
            stage="failed",
            message="Generation failed",
            error=f"{detail} Resumable chunks were kept, so a retry resumes.",
        )


def _start_job(app: FastAPI, job_id: str) -> None:
    task = asyncio.create_task(_run_job(app, job_id), name=f"audiobook-{job_id}")
    app.state.running_tasks.add(task)
    task.add_done_callback(app.state.running_tasks.discard)


def _sse_message(event: str, payload: dict[str, Any], *, event_id: int | None = None) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {data}\n\n"


def create_app(
    *,
    storage_root: Path | None = None,
    preview_root: Path | None = None,
    frontend_dist: Path | None = None,
    desktop_token: str | None = None,
) -> FastAPI:
    """Create an isolated API instance; custom roots keep tests disposable."""
    resolved_storage = _repo_relative(
        storage_root
        or os.environ.get("DOC2AUDIOBOOK_WEB_DATA_DIR", DEFAULT_STORAGE_ROOT)
    )
    resolved_previews = _repo_relative(
        preview_root
        or os.environ.get("DOC2AUDIOBOOK_WEB_PREVIEW_DIR", DEFAULT_PREVIEW_ROOT)
    )
    resolved_frontend = _repo_relative(frontend_dist or DEFAULT_FRONTEND_DIST)
    resolved_previews.mkdir(parents=True, exist_ok=True)
    configured_desktop_token = (
        desktop_token
        if desktop_token is not None
        else os.environ.get(DESKTOP_TOKEN_ENV, "")
    )
    if configured_desktop_token and len(configured_desktop_token) < 32:
        raise ValueError("The desktop bootstrap token must be at least 32 characters.")
    desktop_session = secrets.token_urlsafe(32) if configured_desktop_token else ""

    api = FastAPI(
        title="Doc2Audiobook local API",
        version=SERVICE_VERSION,
        description="Local-only web interface for the Doc2Audiobook engine.",
    )
    api.state.job_store = JobStore(resolved_storage)
    api.state.preview_root = resolved_previews.resolve()
    api.state.preview_locks = {}
    api.state.generation_lock = asyncio.Lock()
    api.state.running_tasks = set()
    api.state.desktop_token = configured_desktop_token
    api.state.desktop_session = desktop_session
    api.state.desktop_bootstrap_used = False

    api.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    api.add_middleware(
        IPv6CompatibleTrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @api.middleware("http")
    async def enforce_browser_origin(request: Request, call_next):
        path = request.scope.get("path", "")
        if configured_desktop_token and _is_api_request_path(path):
            if path != "/api/desktop/session":
                supplied_session = request.cookies.get(DESKTOP_SESSION_COOKIE, "")
                if not hmac.compare_digest(supplied_session, desktop_session):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Desktop session is not authenticated."},
                    )
        if (
            request.method == "POST"
            and path in STATE_CHANGING_PATHS
            and not _origin_allowed(request, request.headers.get("origin"))
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Browser origin is not allowed."},
            )
        return await call_next(request)

    @api.post("/api/desktop/session", include_in_schema=False)
    async def establish_desktop_session(request: Request) -> JSONResponse:
        if not configured_desktop_token:
            raise HTTPException(status_code=404, detail="Not found.")
        supplied = request.headers.get(DESKTOP_TOKEN_HEADER, "")
        if api.state.desktop_bootstrap_used or not hmac.compare_digest(
            supplied, configured_desktop_token
        ):
            raise HTTPException(status_code=401, detail="Desktop bootstrap failed.")
        api.state.desktop_bootstrap_used = True
        response = JSONResponse({"status": "ready", "desktop": True})
        response.set_cookie(
            DESKTOP_SESSION_COOKIE,
            desktop_session,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    @api.middleware("http")
    async def add_desktop_security_headers(request: Request, call_next):
        response = await call_next(request)
        if configured_desktop_token:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                "form-action 'self'; img-src 'self' data:; media-src 'self' blob:; "
                "script-src 'self'; style-src 'self'; connect-src 'self'"
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @api.get("/health", include_in_schema=False)
    @api.get("/api/health", tags=["system"])
    async def health() -> dict[str, Any]:
        # ffmpeg assembles every audiobook and transcodes the offline providers'
        # previews; report it so the UI can warn before a job or preview fails.
        tools = media_tools_status()
        return {
            "status": "ok",
            "service": "doc2audiobook",
            "version": SERVICE_VERSION,
            "ffmpeg": tools["ffmpeg"],
            "ffprobe": tools["ffprobe"],
            "desktop": bool(configured_desktop_token),
        }

    @api.get("/providers", include_in_schema=False)
    @api.get("/api/providers", tags=["voices"])
    async def list_providers() -> dict[str, list[dict[str, Any]]]:
        rows = []
        for provider_id in providers.all_provider_ids():
            provider = providers.get_provider(provider_id)
            rows.append(
                {
                    "id": provider.id,
                    "label": provider.label,
                    "available": provider.is_available(),
                    "capabilities": asdict(provider.capabilities()),
                }
            )
        return {"providers": rows}

    @api.get("/voices", include_in_schema=False)
    async def legacy_voices(
        provider: str = providers.DEFAULT_PROVIDER_ID,
        locale: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        return await voices(provider, locale, q)

    @api.get("/api/providers/{provider_id}/voices", tags=["voices"])
    async def voices(
        provider_id: str,
        locale: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        provider = _require_available(provider_id)
        if not provider.capabilities().supports_voice_list:
            return {"provider": provider_id, "voices": []}
        try:
            catalog = await provider.list_voices()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Voice discovery failed for provider %s", provider_id)
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Could not load voices for '{provider_id}' right now. "
                    "Check the provider setup and try again."
                ),
            ) from exc
        locale_needle = (locale or "").casefold()
        search_needle = (q or "").casefold()
        result = []
        for voice in catalog:
            if locale_needle and voice.locale.casefold() != locale_needle:
                continue
            haystack = f"{voice.id} {voice.label} {voice.gender} {voice.locale}".casefold()
            if search_needle and search_needle not in haystack:
                continue
            result.append(
                {
                    "id": voice.id,
                    "label": voice.label,
                    "gender": voice.gender,
                    "locale": voice.locale,
                    "provider": voice.provider or provider_id,
                }
            )
        result.sort(key=lambda item: (item["locale"], item["label"], item["id"]))
        return {"provider": provider_id, "voices": result}

    @api.post("/preview", include_in_schema=False)
    @api.post("/api/preview", tags=["voices"])
    async def preview(payload: PreviewRequest) -> dict[str, Any]:
        provider = _require_available(payload.provider)
        voice = payload.voice.strip()
        text = payload.text.strip()
        if not voice:
            raise HTTPException(status_code=422, detail="Voice cannot be blank.")
        if not text:
            raise HTTPException(status_code=422, detail="Preview text cannot be blank.")
        caps = provider.capabilities()
        effective_pitch = payload.pitch if caps.supports_pitch else 0
        identity = {
            "schema": 1,
            "provider": provider.id,
            "provider_identity": provider.cache_identity(),
            "voice": voice,
            "rate": payload.rate,
            "pitch": effective_pitch,
            "text": text,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        destination = api.state.preview_root / f"{digest}.mp3"
        marks_path = api.state.preview_root / f"{digest}.marks.json"
        marks: list[dict[str, Any]] | None = None
        lock = api.state.preview_locks.setdefault(digest, asyncio.Lock())
        async with lock:
            if destination.is_file() and destination.stat().st_size:
                # Cache hit — reuse audio and any timings captured on first render.
                if marks_path.is_file():
                    try:
                        marks = json.loads(marks_path.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001 - stale/corrupt cache is non-fatal
                        marks = None
            else:
                temporary = destination.with_name(f"{digest}.{uuid.uuid4().hex}.part.mp3")
                try:
                    async with api.state.generation_lock:
                        if caps.supports_word_marks:
                            # Providers with real word timings (edge-tts) render and
                            # return marks in one pass — no double synthesis.
                            marks = await provider.synthesize_with_marks(
                                text,
                                voice,
                                temporary,
                                rate=_rate(payload.rate),
                                pitch=_pitch(effective_pitch),
                            )
                        else:
                            # Everyone else keeps the engine path (handles transcode).
                            await narrate.synthesize_to_file(
                                text,
                                voice,
                                _rate(payload.rate),
                                _pitch(effective_pitch),
                                temporary,
                                provider=provider,
                            )
                    if not temporary.is_file() or temporary.stat().st_size == 0:
                        raise RuntimeError("The provider returned an empty preview.")
                    temporary.replace(destination)
                    if marks:
                        marks_path.write_text(
                            json.dumps(marks, ensure_ascii=False), encoding="utf-8"
                        )
                except Exception as exc:  # noqa: BLE001
                    temporary.unlink(missing_ok=True)
                    logger.exception(
                        "Voice preview failed for provider %s", provider.id
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=_synthesis_failure_detail(exc, action="create the preview"),
                    ) from exc
        return {
            "audio_url": f"/api/preview/file/{digest}.mp3",
            "text": text,
            "marks": marks,
        }

    @api.get("/api/preview/file/{name}", tags=["voices"], response_class=FileResponse)
    async def preview_file(name: str) -> FileResponse:
        # Serve only cached preview audio, addressed by its content digest, and
        # never let a crafted name escape the preview directory.
        if not re.fullmatch(r"[0-9a-f]{64}\.mp3", name):
            raise HTTPException(status_code=404, detail="Preview not found.")
        candidate = (api.state.preview_root / name).resolve()
        if candidate.parent != api.state.preview_root or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Preview not found.")
        return FileResponse(candidate, media_type="audio/mpeg", filename=name)

    @api.post("/jobs", include_in_schema=False, status_code=202)
    @api.post("/api/jobs", tags=["jobs"], status_code=202)
    async def create_job(
        document: UploadFile | None = File(None),
        desktop_path: str | None = Form(None),
        provider: str = Form(providers.DEFAULT_PROVIDER_ID),
        voice: str = Form(...),
        rate: int = Form(-4),
        pitch: int = Form(0),
        output_format: str = Form("mp3"),
        max_chars: int | None = Form(None),
        keep_chunks: bool = Form(False),
        rules: str = Form("[]"),
    ) -> dict[str, Any]:
        engine = _require_available(provider)
        if output_format not in narrate.SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported output format '{output_format}'.",
            )
        if not -100 <= rate <= 100 or not -100 <= pitch <= 100:
            raise HTTPException(
                status_code=422,
                detail="Rate and pitch must be between -100 and 100.",
            )
        if max_chars is not None and not 250 <= max_chars <= 10_000:
            raise HTTPException(
                status_code=422,
                detail="max_chars must be between 250 and 10000.",
            )
        # Omitted means "whatever this engine wants". Resolve it here rather
        # than in the worker so the stored job settings record the real value.
        max_chars = narrate.resolve_max_chars(engine, max_chars)
        voice = voice.strip()
        if not voice:
            raise HTTPException(status_code=422, detail="Voice cannot be blank.")
        selected_path: Path | None = None
        if document is not None and desktop_path:
            await document.close()
            raise HTTPException(
                status_code=422,
                detail="Choose either an uploaded document or a desktop path, not both.",
            )
        if document is not None:
            filename = Path(document.filename or "").name
        elif desktop_path:
            if not configured_desktop_token:
                raise HTTPException(
                    status_code=422,
                    detail="Native document paths are available only in the desktop app.",
                )
            selected_path = Path(desktop_path)
            if not selected_path.is_absolute():
                raise HTTPException(
                    status_code=422, detail="The selected desktop path must be absolute."
                )
            filename = selected_path.name
        else:
            raise HTTPException(status_code=422, detail="Choose a document.")
        suffix = Path(filename).suffix.lower()
        if suffix not in narrate.EXTRACTORS:
            allowed = ", ".join(sorted(narrate.EXTRACTORS))
            if document is not None:
                await document.close()
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported document type '{suffix or '(none)'}'. Use: {allowed}.",
            )

        parsed_rules = _parse_rules(rules)
        caps = engine.capabilities()
        effective_pitch = pitch if caps.supports_pitch else 0
        public_rules = [
            {"from": source, "to": target} for source, target in parsed_rules
        ]
        settings: dict[str, Any] = {
            "provider": provider,
            "voice": voice,
            "rate": rate,
            "pitch": effective_pitch,
            "output_format": output_format,
            "max_chars": max_chars,
            # Retention is always enabled for web jobs so a failed or repeated
            # request can safely resume. The accepted form field keeps the API
            # forward-compatible with the prototype's control.
            "keep_chunks": True,
            "rules": public_rules,
        }

        store: JobStore = api.state.job_store
        temporary = store.uploads_root / f"{uuid.uuid4().hex}.upload"
        if document is not None:
            content_hash = await _save_upload(document, temporary)
        else:
            assert selected_path is not None
            content_hash = await _save_desktop_document(selected_path, temporary)
        render_key = _render_identity(content_hash, suffix, engine, settings)
        job_id = render_key[:32]
        job_dir = store.jobs_root / job_id
        input_path = job_dir / f"source{suffix}"
        output_path = job_dir / f"{_safe_stem(filename)}.{output_format}"
        record = JobRecord(
            id=job_id,
            filename=filename,
            settings=settings,
            input_path=input_path,
            output_path=output_path,
            work_dir=job_dir / "chunks",
        )
        selected, is_new = store.reserve(record, max_active=MAX_ACTIVE_JOBS)
        if selected is None:
            temporary.unlink(missing_ok=True)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many active jobs (limit {MAX_ACTIVE_JOBS}). "
                    "Wait for one to finish, then retry."
                ),
            )
        if not is_new:
            temporary.unlink(missing_ok=True)
            return selected.public()

        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            temporary.replace(input_path)
        except Exception:
            store.discard(job_id)
            temporary.unlink(missing_ok=True)
            raise
        store.persist(record)
        _start_job(api, job_id)
        return record.public()

    @api.get("/api/jobs", tags=["jobs"])
    async def list_jobs() -> dict[str, list[dict[str, Any]]]:
        """All known jobs, newest first — survives restarts via persisted state."""
        return {"jobs": api.state.job_store.list_public()}

    @api.get("/jobs/{job_id}", include_in_schema=False)
    @api.get("/api/jobs/{job_id}", tags=["jobs"])
    async def job_status(job_id: str) -> dict[str, Any]:
        snapshot = api.state.job_store.snapshot(job_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        return snapshot[0]

    @api.get("/jobs/{job_id}/events", include_in_schema=False)
    @api.get("/api/jobs/{job_id}/events", tags=["jobs"])
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        if api.state.job_store.get(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found.")

        # Resume after the last event the client already saw (R-009). Each event
        # id is the job's monotonic version, so a reconnect never re-delivers a
        # snapshot the client has, and an already-terminal job closes cleanly.
        raw_last_id = request.headers.get("last-event-id")
        try:
            resume_from = int(raw_last_id) if raw_last_id else -1
        except ValueError:
            resume_from = -1

        async def stream():
            last_version = resume_from
            idle = 0.0
            while True:
                snapshot = api.state.job_store.snapshot(job_id)
                if snapshot is None:
                    return
                payload, version = snapshot
                if version != last_version:
                    status = payload["status"]
                    event = (
                        "complete" if status == "completed"
                        else "error" if status == "failed"
                        else "progress"
                    )
                    yield _sse_message(event, payload, event_id=version)
                    last_version = version
                    idle = 0.0
                    if status in TERMINAL_STATUSES:
                        return
                elif payload["status"] in TERMINAL_STATUSES:
                    # Client already holds the terminal snapshot; close the stream
                    # instead of holding it open with heartbeats forever.
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(_SSE_POLL_SECONDS)
                idle += _SSE_POLL_SECONDS
                if idle >= SSE_HEARTBEAT_SECONDS:
                    yield _SSE_HEARTBEAT_FRAME
                    idle = 0.0

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @api.get("/jobs/{job_id}/output", include_in_schema=False)
    @api.get("/api/jobs/{job_id}/output", tags=["jobs"], response_class=FileResponse)
    async def job_output(job_id: str) -> FileResponse:
        job = api.state.job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"Job is '{job.status}'; output is not ready.",
            )
        if not job.output_path.is_file():
            raise HTTPException(status_code=410, detail="The output file is missing.")
        media_types = {
            "mp3": "audio/mpeg",
            "m4a": "audio/mp4",
            "wav": "audio/wav",
        }
        return FileResponse(
            job.output_path,
            media_type=media_types[job.settings["output_format"]],
            filename=job.output_path.name,
        )

    # Keep this mount last: explicit API routes must win, and SPAStaticFiles
    # deliberately refuses to turn unknown /api paths into index.html.
    if (resolved_frontend / "index.html").is_file():
        api.mount(
            "/",
            SPAStaticFiles(directory=resolved_frontend, html=True),
            name="web-frontend",
        )

    return api


app = create_app()
