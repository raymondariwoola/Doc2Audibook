#!/usr/bin/env python3
"""
narrate — convert a document (.docx, .pdf, .txt, .md) into a natural-sounding
narrated audiobook-style MP3 using Microsoft's free `edge-tts` neural voices.

No paid API key required. ffmpeg is used for final assembly (stream copy).

See README.md for installation and usage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------

DEFAULT_VOICE = "en-US-AndrewNeural"
DEFAULT_RATE = "-4%"          # a touch slower than 100% reads more naturally
MAX_CHUNK_CHARS = 3000        # keep each edge-tts request comfortably small
PAUSE_CUE = "…"          # an ellipsis on its own line = a spoken pause
CACHE_SCHEMA_VERSION = 1
CACHE_MANIFEST_NAME = ".narrate-cache.json"


# ----------------------------------------------------------------------------
# Extraction — produce an ordered list of "blocks" preserving reading order.
# Each block is a dict: {"type": "heading"|"para", "level": int, "text": str}
# Headings carry their level (1 = major section); paras use level 0.
# Table rows are emitted as plain paras ("term. definition.").
# ----------------------------------------------------------------------------


def _clean_ws(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("\xa0", " ")).strip()


def extract_docx(path: Path) -> list[dict]:
    """Walk the docx body in true document order so tables are read in place."""
    import docx
    from docx.document import Document as _Doc
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(str(path))
    body = doc.element.body
    blocks: list[dict] = []

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = _clean_ws(para.text)
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style else ""
            m = re.match(r"heading\s+(\d+)", style)
            if m:
                blocks.append({"type": "heading", "level": int(m.group(1)), "text": text})
            elif style == "title":
                blocks.append({"type": "heading", "level": 1, "text": text})
            else:
                blocks.append({"type": "para", "level": 0, "text": text})
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            blocks.extend(_docx_table_to_blocks(table))
    return blocks


def _docx_table_to_blocks(table) -> list[dict]:
    """Read a table row by row as 'term. definition.' pairs, skipping the header row."""
    blocks: list[dict] = []
    rows = table.rows
    for row in rows[1:]:  # skip header row
        cells = [_clean_ws(c.text) for c in row.cells]
        # De-duplicate horizontally-merged cells that repeat the same text.
        deduped: list[str] = []
        for c in cells:
            if not deduped or deduped[-1] != c:
                deduped.append(c)
        cells = [c for c in deduped if c]
        if not cells:
            continue
        term = cells[0]
        definition = " ".join(cells[1:]).strip()
        if definition:
            text = f"{_ensure_period(term)} {_ensure_period(definition)}"
        else:
            text = _ensure_period(term)
        blocks.append({"type": "para", "level": 0, "text": text})
    return blocks


def _ensure_period(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in ".!?:;":
        text += "."
    return text


def extract_pdf(path: Path) -> list[dict]:
    """Extract PDF text preserving reading order, one block per non-empty line group."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[dict] = []
    for page in reader.pages:
        raw = page.extract_text() or ""
        # Split into paragraphs on blank lines; collapse soft-wrapped lines.
        for para in re.split(r"\n\s*\n", raw):
            para = _clean_ws(para.replace("\n", " "))
            if para:
                blocks.append({"type": "para", "level": 0, "text": para})
    return blocks


def extract_txt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict] = []
    for para in re.split(r"\n\s*\n", raw):
        para = _clean_ws(para.replace("\n", " "))
        if para:
            blocks.append({"type": "para", "level": 0, "text": para})
    return blocks


def extract_md(path: Path) -> list[dict]:
    """Parse Markdown: ATX headings become headings, pipe tables become rows."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    blocks: list[dict] = []
    para_buf: list[str] = []
    table_buf: list[str] = []

    def flush_para():
        nonlocal para_buf
        if para_buf:
            text = _clean_ws(" ".join(para_buf))
            if text:
                blocks.append({"type": "para", "level": 0, "text": text})
            para_buf = []

    def flush_table():
        nonlocal table_buf
        if table_buf:
            blocks.extend(_md_table_to_blocks(table_buf))
            table_buf = []

    for line in lines:
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        is_table_row = stripped.startswith("|") and stripped.count("|") >= 2

        if is_table_row:
            flush_para()
            table_buf.append(stripped)
            continue
        else:
            flush_table()

        if heading:
            flush_para()
            blocks.append(
                {"type": "heading", "level": len(heading.group(1)), "text": _clean_ws(heading.group(2))}
            )
        elif not stripped:
            flush_para()
        else:
            para_buf.append(stripped)

    flush_table()
    flush_para()
    return blocks


def _md_table_to_blocks(rows: list[str]) -> list[dict]:
    """Convert a markdown pipe table to 'term. definition.' rows, skipping header + separator."""
    parsed: list[list[str]] = []
    for row in rows:
        # Ignore the |---|---| separator line.
        if re.match(r"^\|?\s*:?-{2,}", row.replace(" ", "")) or re.match(
            r"^[\s\|:\-]+$", row
        ):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        cells = [c for c in cells if c != ""]
        if cells:
            parsed.append(cells)
    blocks: list[dict] = []
    for cells in parsed[1:]:  # skip header row
        term = cells[0]
        definition = " ".join(cells[1:]).strip()
        text = f"{_ensure_period(term)} {_ensure_period(definition)}" if definition else _ensure_period(term)
        blocks.append({"type": "para", "level": 0, "text": text})
    return blocks


EXTRACTORS = {
    ".docx": extract_docx,
    ".pdf": extract_pdf,
    ".txt": extract_txt,
    ".md": extract_md,
    ".markdown": extract_md,
}


def extract_blocks(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext not in EXTRACTORS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(EXTRACTORS))}"
        )
    return EXTRACTORS[ext](path)


# ----------------------------------------------------------------------------
# Speech-friendly cleanup
# ----------------------------------------------------------------------------

# Standalone symbols -> spoken words (order matters: multi-char first).
SYMBOL_REPLACEMENTS: list[tuple[str, str]] = [
    ("→", " leads to "),
    ("←", " comes from "),
    ("↔", " corresponds to "),
    ("⇒", " implies "),
    ("≈", " approximately equals "),
    ("≠", " does not equal "),
    ("≤", " is less than or equal to "),
    ("≥", " is greater than or equal to "),
    ("±", " plus or minus "),
    ("×", " times "),
    ("÷", " divided by "),
    ("≡", " is equivalent to "),
    ("∞", " infinity "),
    ("°", " degrees "),
    ("&", " and "),
    ("@", " at "),
    ("€", " euros "),
    ("£", " pounds "),
    ("$", " dollars "),
]

# Spaced binary operators -> words. Requires whitespace on both sides so we
# don't mangle hyphenated words, negative numbers, or ranges written as A-B.
SPACED_OPERATORS: list[tuple[str, str]] = [
    (r"\s=\s", " equals "),
    (r"\s\+\s", " plus "),
    (r"\s-\s", " minus "),
    (r"\s\*\s", " times "),
    (r"\s/\s", " divided by "),
    (r"\s<\s", " is less than "),
    (r"\s>\s", " is greater than "),
]


def _spoken_percent(text: str) -> str:
    # "50%" -> "50 percent", "%" on its own -> " percent"
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    return text.replace("%", " percent")


def clean_for_speech(text: str, user_rules: list[tuple[str, str]]) -> str:
    """Apply user rules, strip formatting artifacts, and verbalize symbols."""
    # 1. User-supplied find/replace rules run first so document-specific labels
    #    can be reworded before any other transformation touches them.
    for src, dst in user_rules:
        text = text.replace(src, dst)

    # 2. Strip markdown / formatting artifacts that would be read literally.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)          # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)        # links -> label
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)          # inline code
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)   # bold/italic
    text = text.replace("|", " ")                               # stray table pipes
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)  # list bullets
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)      # blockquotes
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)    # stray heading #

    # 3. Verbalize symbols and operators.
    text = _spoken_percent(text)
    for src, dst in SYMBOL_REPLACEMENTS:
        text = text.replace(src, dst)
    for pattern, dst in SPACED_OPERATORS:
        text = re.sub(pattern, dst, text)

    # 4. Collapse whitespace introduced by the substitutions.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def blocks_to_paragraphs(blocks: list[dict], user_rules: list[tuple[str, str]]) -> list[str]:
    """Turn ordered blocks into cleaned paragraphs, inserting a pause cue
    before each major (Heading 1 / Title) section break."""
    paragraphs: list[str] = []
    seen_first_heading = False
    for block in blocks:
        cleaned = clean_for_speech(block["text"], user_rules)
        if not cleaned:
            continue
        if block["type"] == "heading":
            # Pause before major section breaks (but not before the very first).
            if block["level"] <= 1:
                if seen_first_heading:
                    paragraphs.append(PAUSE_CUE)
                seen_first_heading = True
            paragraphs.append(_ensure_period(cleaned))
        else:
            paragraphs.append(cleaned)
    return paragraphs


# ----------------------------------------------------------------------------
# Chunking — group paragraphs under MAX_CHUNK_CHARS, never splitting a sentence.
# ----------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _split_long_paragraph(para: str, limit: int) -> list[str]:
    """Split an over-limit paragraph on sentence boundaries only."""
    sentences = _SENTENCE_SPLIT.split(para)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def chunk_paragraphs(paragraphs: Iterable[str], limit: int = MAX_CHUNK_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Handle any single paragraph that is itself over the limit.
        units = [para] if len(para) <= limit else _split_long_paragraph(para, limit)
        for unit in units:
            candidate = f"{current}\n\n{unit}" if current else unit
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = unit
    if current:
        chunks.append(current)
    return chunks


# ----------------------------------------------------------------------------
# TTS generation — delegated to the provider layer (providers.py). edge-tts is
# the default provider; the functions below keep their historical signatures so
# the CLI and the Gradio prototype are unaffected by the abstraction.
# ----------------------------------------------------------------------------

import providers  # noqa: E402
from providers import (  # noqa: E402  (re-exported for backward compatibility)
    is_ssl_cert_error as _is_ssl_cert_error,
    apply_ssl_fallback as _apply_ssl_fallback,
)

DEFAULT_PITCH = "+0Hz"          # natural pitch; edge-tts accepts e.g. "+5Hz"


async def synthesize_to_file(
    text: str, voice: str, rate: str, pitch: str, out_path: Path,
    provider: "providers.TTSProvider | None" = None,
) -> None:
    """Synthesize one utterance to `out_path` via the given provider (default:
    edge-tts). Kept for backward compatibility and used for voice previews."""
    prov = provider or providers.default_provider()
    await prov.synthesize(text, voice, out_path, rate=rate, pitch=pitch)


async def generate_chunks(
    chunks: list[str],
    voice: str,
    rate: str,
    pitch: str,
    work_dir: Path,
    on_chunk=None,
    provider: "providers.TTSProvider | None" = None,
) -> list[Path]:
    """Synthesize each chunk to its own MP3. Resumable: non-empty existing
    chunk files are skipped. `on_chunk(i, total, chars, status)` is called per
    chunk with status in {"skipped", "done"} so any UI can render progress."""
    prov = provider or providers.default_provider()
    work_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    total = len(chunks)

    for i, text in enumerate(chunks, start=1):
        out_path = work_dir / f"chunk_{i:04d}.mp3"
        paths.append(out_path)

        if out_path.exists() and out_path.stat().st_size > 0:
            if on_chunk:
                on_chunk(i, total, len(text), "skipped")
            continue

        await prov.synthesize(text, voice, out_path, rate=rate, pitch=pitch)
        if on_chunk:
            on_chunk(i, total, len(text), "done")

    return paths


def narration_fingerprint(
    chunks: list[str],
    *,
    provider: "providers.TTSProvider",
    voice: str,
    rate: str,
    pitch: str,
    max_chars: int,
) -> str:
    """Return a stable identity for every setting that affects chunk audio."""
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "chunks": chunks,
        "provider": provider.id,
        "provider_identity": provider.cache_identity(),
        "voice": voice,
        "rate": rate,
        "pitch": pitch,
        "max_chars": max_chars,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepare_chunk_cache(work_dir: Path, fingerprint: str) -> str:
    """Prepare a work directory and invalidate only stale generated artifacts.

    Returns ``"reused"`` when the manifest matches, otherwise ``"initialized"``
    or ``"invalidated"``. A manifest is written before synthesis begins so an
    interrupted render remains safely resumable.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / CACHE_MANIFEST_NAME
    previous = None
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
    if previous == {"schema": CACHE_SCHEMA_VERSION, "fingerprint": fingerprint}:
        return "reused"

    had_generated_files = False
    for pattern in ("chunk_*", "concat_list.txt", "_concat.mp3"):
        for artifact in work_dir.glob(pattern):
            if artifact.is_file():
                artifact.unlink()
                had_generated_files = True

    manifest = {"schema": CACHE_SCHEMA_VERSION, "fingerprint": fingerprint}
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".part")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    tmp.replace(manifest_path)
    return "invalidated" if had_generated_files else "initialized"


async def fetch_voices(provider_id: str = providers.DEFAULT_PROVIDER_ID) -> list[dict]:
    """Return a provider's voice catalog as plain dicts (ShortName/Gender/Locale),
    the shape the CLI and prototype already consume. Reusable by any UI."""
    voices = await providers.get_provider(provider_id).list_voices()
    result: list[dict] = []
    for v in voices:
        result.append(v.raw or {
            "ShortName": v.id, "Gender": v.gender, "Locale": v.locale,
        })
    return result


# ----------------------------------------------------------------------------
# Voice listing
# ----------------------------------------------------------------------------


async def list_voices(filter_substr: str | None,
                      provider_id: str = providers.DEFAULT_PROVIDER_ID) -> None:
    voices = await fetch_voices(provider_id)
    voices.sort(key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
    needle = (filter_substr or "").lower()
    shown = 0
    print(f"{'ShortName':<32} {'Gender':<8} Locale")
    print("-" * 60)
    for v in voices:
        short = v.get("ShortName", "")
        if needle and needle not in short.lower() and needle not in v.get("Locale", "").lower():
            continue
        print(f"{short:<32} {v.get('Gender', ''):<8} {v.get('Locale', '')}")
        shown += 1
    print("-" * 60)
    print(f"{shown} voice(s)"
          + (f" matching '{filter_substr}'" if filter_substr else " total")
          + ".  Pass one with --voice.")


# ----------------------------------------------------------------------------
# Final assembly with ffmpeg (stream copy, no re-encode)
# ----------------------------------------------------------------------------


def assemble(chunk_paths: list[Path], output: Path, work_dir: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg was not found on PATH. Install it (see README) and re-run — "
            "your chunk files are preserved, so this will resume instantly."
        )
    list_file = work_dir / "concat_list.txt"
    with list_file.open("w", encoding="utf-8") as fh:
        for p in chunk_paths:
            # ffmpeg concat demuxer: single-quote and escape any quotes in path.
            safe = str(p.resolve()).replace("'", r"'\''")
            fh.write(f"file '{safe}'\n")

    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr.strip()}")


# Container/codec settings per output format. mp3 is a lossless stream copy of
# the edge-tts chunks; the others transcode the concatenated audio once.
SUPPORTED_FORMATS = {
    "mp3": ["-c", "copy"],
    "m4a": ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
    "wav": ["-c:a", "pcm_s16le"],
}


def _transcode(src: Path, dst: Path, fmt: str) -> None:
    """Re-encode the concatenated audio into a non-mp3 container via ffmpeg."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), *SUPPORTED_FORMATS[fmt], str(dst)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg transcode to {fmt} failed:\n{result.stderr.strip()}")


def encode_output(chunk_paths: list[Path], output: Path, work_dir: Path, fmt: str) -> None:
    """Assemble chunks into the final file in the requested format."""
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format '{fmt}'. Choose one of: "
                         f"{', '.join(SUPPORTED_FORMATS)}")
    if fmt == "mp3":
        assemble(chunk_paths, output, work_dir)         # stream copy, no re-encode
    else:
        concat = work_dir / "_concat.mp3"
        assemble(chunk_paths, concat, work_dir)
        _transcode(concat, output, fmt)
        concat.unlink(missing_ok=True)


def probe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _fmt_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# ----------------------------------------------------------------------------
# Rule parsing
# ----------------------------------------------------------------------------


def parse_rules(replace_args: list[str] | None, rules_file: str | None) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    if rules_file:
        data = json.loads(Path(rules_file).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            rules.extend((str(k), str(v)) for k, v in data.items())
        elif isinstance(data, list):
            for item in data:
                rules.append((str(item["from"]), str(item["to"])))
        else:
            raise ValueError("--rules JSON must be an object or a list of {from,to} objects")
    for spec in replace_args or []:
        if "=" not in spec:
            raise ValueError(f"--replace must be 'from=to', got: {spec!r}")
        src, dst = spec.split("=", 1)
        rules.append((src, dst))
    return rules


# ----------------------------------------------------------------------------
# Reusable pipeline core — shared by the CLI and every future UI (Gradio, web,
# desktop). UI-agnostic: takes optional progress callbacks, returns a result.
# ----------------------------------------------------------------------------


@dataclass
class AudiobookResult:
    output: Path
    fmt: str
    duration: Optional[float]
    size: int
    n_blocks: int
    n_paragraphs: int
    n_chunks: int
    total_chars: int
    work_dir: Path


async def build_audiobook_async(
    input_path: Path,
    output: Path,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    fmt: str = "mp3",
    provider: str = providers.DEFAULT_PROVIDER_ID,
    user_rules: Optional[list[tuple[str, str]]] = None,
    work_dir: Optional[Path] = None,
    keep_chunks: bool = False,
    max_chars: int = MAX_CHUNK_CHARS,
    on_stage: Optional[Callable[[str, dict], None]] = None,
    on_chunk: Optional[Callable[[int, int, int, str], None]] = None,
) -> AudiobookResult:
    """Run the full document -> narrated audio pipeline asynchronously.

    `provider` selects the TTS engine (default "edge"; see providers.py).
    `on_stage(stage, info)` fires at "extracted", "assembling", and "done".
    `on_chunk(i, total, chars, status)` fires per chunk ("skipped"/"done").
    Raises ValueError if the document yields no readable text.
    """
    input_path = Path(input_path)
    output = Path(output)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format '{fmt}'. Choose one of: "
                         f"{', '.join(SUPPORTED_FORMATS)}")
    prov = providers.get_provider(provider)
    if work_dir is None:
        work_dir = output.with_name(output.stem + "_chunks")
    work_dir = Path(work_dir)

    blocks = await asyncio.to_thread(extract_blocks, input_path)
    paragraphs = blocks_to_paragraphs(blocks, user_rules or [])
    chunks = chunk_paragraphs(paragraphs, max_chars)
    if not chunks:
        raise ValueError("No readable text was extracted from the document.")
    total_chars = sum(len(c) for c in chunks)
    fingerprint = narration_fingerprint(
        chunks,
        provider=prov,
        voice=voice,
        rate=rate,
        pitch=pitch,
        max_chars=max_chars,
    )
    cache_status = prepare_chunk_cache(work_dir, fingerprint)

    if on_stage:
        on_stage("extracted", {
            "blocks": len(blocks), "paragraphs": len(paragraphs),
            "chunks": len(chunks), "chars": total_chars, "work_dir": str(work_dir),
            "cache_status": cache_status,
        })

    chunk_paths = await generate_chunks(
        chunks, voice, rate, pitch, work_dir,
        on_chunk=on_chunk, provider=prov,
    )

    if on_stage:
        on_stage("assembling", {"output": str(output), "fmt": fmt})
    await asyncio.to_thread(encode_output, chunk_paths, output, work_dir, fmt)

    size = output.stat().st_size
    duration = await asyncio.to_thread(probe_duration, output)

    if not keep_chunks:
        shutil.rmtree(work_dir, ignore_errors=True)

    result = AudiobookResult(
        output=output, fmt=fmt, duration=duration, size=size,
        n_blocks=len(blocks), n_paragraphs=len(paragraphs),
        n_chunks=len(chunks), total_chars=total_chars, work_dir=work_dir,
    )
    if on_stage:
        on_stage("done", {"duration": duration, "size": size})
    return result


def build_audiobook(
    input_path: Path,
    output: Path,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    fmt: str = "mp3",
    provider: str = providers.DEFAULT_PROVIDER_ID,
    user_rules: Optional[list[tuple[str, str]]] = None,
    work_dir: Optional[Path] = None,
    keep_chunks: bool = False,
    max_chars: int = MAX_CHUNK_CHARS,
    on_stage: Optional[Callable[[str, dict], None]] = None,
    on_chunk: Optional[Callable[[int, int, int, str], None]] = None,
) -> AudiobookResult:
    """Synchronous compatibility wrapper around :func:`build_audiobook_async`."""
    return asyncio.run(build_audiobook_async(
        input_path,
        output,
        voice=voice,
        rate=rate,
        pitch=pitch,
        fmt=fmt,
        provider=provider,
        user_rules=user_rules,
        work_dir=work_dir,
        keep_chunks=keep_chunks,
        max_chars=max_chars,
        on_stage=on_stage,
        on_chunk=on_chunk,
    ))


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="narrate",
        description="Convert a .docx/.pdf/.txt/.md document into a narrated MP3 "
                    "using free edge-tts neural voices.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  narrate report.docx\n"
            "  narrate book.pdf --output book.mp3 --voice en-US-AriaNeural --rate -8%\n"
            "  narrate notes.md --replace 'NOTE:=Here is an important note.' --keep-chunks\n"
            "  narrate --list-voices en-US\n"
        ),
    )
    p.add_argument("input", nargs="?", help="input document (.docx, .pdf, .txt, .md)")
    p.add_argument("-o", "--output", help="output MP3 path (default: <input>.mp3)")
    p.add_argument("--provider", choices=providers.all_provider_ids(),
                   default=providers.DEFAULT_PROVIDER_ID,
                   help=f"TTS provider (default: {providers.DEFAULT_PROVIDER_ID}). "
                        "'openai' needs OPENAI_API_KEY and is experimental.")
    p.add_argument("--voice", default=DEFAULT_VOICE, help=f"voice name (default: {DEFAULT_VOICE})")
    p.add_argument("--rate", default=DEFAULT_RATE,
                   help="speech rate, e.g. -10%% or +5%% (default: "
                        + DEFAULT_RATE.replace("%", "%%") + ")")
    p.add_argument("--pitch", default=DEFAULT_PITCH,
                   help=f"voice pitch, e.g. -5Hz or +10Hz (default: {DEFAULT_PITCH})")
    p.add_argument("--format", choices=sorted(SUPPORTED_FORMATS), default="mp3",
                   help="output audio format when --output is omitted (default: mp3)")
    p.add_argument("--list-voices", nargs="?", const="", metavar="FILTER",
                   help="list available voices (optionally filter by locale/name substring) and exit")
    p.add_argument("--replace", action="append", metavar="FROM=TO",
                   help="find/replace rule for speech (repeatable)")
    p.add_argument("--rules", metavar="FILE",
                   help="JSON file of find/replace rules ({\"from\":\"to\"} object or [{from,to}] list)")
    p.add_argument("--work-dir", help="folder for intermediate chunk MP3s (default: <output>_chunks)")
    p.add_argument("--keep-chunks", action="store_true", help="keep intermediate chunk files")
    p.add_argument("--max-chars", type=int, default=MAX_CHUNK_CHARS,
                   help=f"max characters per chunk (default: {MAX_CHUNK_CHARS})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --list-voices short-circuits everything else.
    if args.list_voices is not None:
        asyncio.run(list_voices(args.list_voices or None, args.provider))
        return 0

    if not args.input:
        build_parser().error("input file is required (or use --list-voices)")

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 2

    # --output extension is the source of truth for format; else --format flag.
    if args.output:
        output = Path(args.output)
        fmt = output.suffix.lower().lstrip(".") or "mp3"
    else:
        fmt = args.format
        output = input_path.with_suffix("." + fmt)
    if fmt not in SUPPORTED_FORMATS:
        print(f"error: unsupported output format '{fmt}'. Choose one of: "
              f"{', '.join(sorted(SUPPORTED_FORMATS))}", file=sys.stderr)
        return 2
    work_dir = Path(args.work_dir) if args.work_dir else output.with_name(output.stem + "_chunks")

    try:
        user_rules = parse_rules(args.replace, args.rules)
    except Exception as exc:  # noqa: BLE001
        print(f"error parsing rules: {exc}", file=sys.stderr)
        return 2

    print(f"Reading    : {input_path}  ({input_path.suffix.lower()})")
    print(f"Provider   : {args.provider}")
    print(f"Voice      : {args.voice}   Rate: {args.rate}   Pitch: {args.pitch}   Format: {fmt}")

    def on_stage(stage: str, info: dict) -> None:
        if stage == "extracted":
            print(f"Extracted  : {info['blocks']} blocks -> {info['paragraphs']} paragraphs "
                  f"-> {info['chunks']} chunks ({info['chars']:,} chars)")
            print(f"Work dir   : {info['work_dir']}  (resumable — existing chunks are skipped)")
            if info.get("cache_status") == "invalidated":
                print("Cache      : audio inputs changed; stale generated chunks were cleared")
            print("Synthesizing chunks:")
        elif stage == "assembling":
            verb = "stream copy" if info["fmt"] == "mp3" else f"transcode -> {info['fmt']}"
            print(f"Assembling : {info['output']}  (ffmpeg {verb})")

    def on_chunk(i: int, total: int, chars: int, status: str) -> None:
        label = "skipped (already exists)" if status == "skipped" else "done"
        print(f"  [{i:>3}/{total}] {chars:>4} chars  — {label}")

    try:
        result = build_audiobook(
            input_path, output,
            voice=args.voice, rate=args.rate, pitch=args.pitch, fmt=fmt,
            provider=args.provider,
            user_rules=user_rules, work_dir=work_dir,
            keep_chunks=args.keep_chunks, max_chars=args.max_chars,
            on_stage=on_stage, on_chunk=on_chunk,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nerror during synthesis: {exc}", file=sys.stderr)
        print("Re-run the same command to resume from the last completed chunk.", file=sys.stderr)
        return 1

    dur_str = (_fmt_duration(result.duration) if result.duration is not None
               else "unknown (ffprobe not found)")
    print("-" * 60)
    print(f"Done  ->  {result.output}")
    print(f"Duration : {dur_str}")
    print(f"Size     : {_fmt_size(result.size)}")
    if args.keep_chunks:
        print(f"Chunks kept in: {result.work_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
