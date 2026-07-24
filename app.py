#!/usr/bin/env python3
"""
Doc2Audiobook — Gradio prototype UI (Phase 1 of ROADMAP.md).

A thin browser front-end over the tested `narrate.py` engine: attach a document,
browse voices and hear a sample before committing, tune pitch & pace, pick an
output format, and download the narrated audiobook.

Run:
    python app.py            # opens http://127.0.0.1:7860
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import gradio as gr

import narrate

# ----------------------------------------------------------------------------
# Config / constants
# ----------------------------------------------------------------------------

SAMPLE_TEXT = (
    "Hello — this is a preview of how your audiobook will sound. "
    "The quick brown fox jumps over the lazy dog."
)
CACHE_DIR = Path(".narrate_cache")      # resumable chunk cache, keyed by settings
OUTPUT_DIR = Path("outputs")            # generated audiobooks live here
REC_RATE = -4                           # recommended defaults
REC_PITCH = 0
FORMATS = ["mp3", "m4a", "wav"]


# ----------------------------------------------------------------------------
# Voice catalog (loaded once at startup, with graceful offline handling)
# ----------------------------------------------------------------------------

import asyncio

_VOICE_ROWS: list[dict] = []
_LABEL_TO_SHORT: dict[str, str] = {}
_LOAD_ERROR: str | None = None


def _voice_label(v: dict) -> str:
    return f"{v.get('ShortName', '?')}  ·  {v.get('Gender', '?')}, {v.get('Locale', '?')}"


def _load_voices() -> None:
    global _VOICE_ROWS, _LABEL_TO_SHORT, _LOAD_ERROR
    try:
        rows = asyncio.run(narrate.fetch_voices())
        rows.sort(key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
        _VOICE_ROWS = rows
        _LABEL_TO_SHORT = {_voice_label(v): v["ShortName"] for v in rows}
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = str(exc)


_load_voices()


def _locales() -> list[str]:
    return sorted({v.get("Locale", "") for v in _VOICE_ROWS if v.get("Locale")})


def _labels_for(locale_filter: str) -> list[str]:
    rows = _VOICE_ROWS
    if locale_filter and locale_filter != "All":
        rows = [v for v in rows if v.get("Locale") == locale_filter]
    return [_voice_label(v) for v in rows]


def _default_label() -> str:
    for label, short in _LABEL_TO_SHORT.items():
        if short == narrate.DEFAULT_VOICE:
            return label
    labels = list(_LABEL_TO_SHORT)
    return labels[0] if labels else ""


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _pct(v: int) -> str:
    return f"{int(v):+d}%"


def _hz(v: int) -> str:
    return f"{int(v):+d}Hz"


def _short_from_label(label: str) -> str:
    return _LABEL_TO_SHORT.get(label) or (label.split()[0] if label else narrate.DEFAULT_VOICE)


def _cache_dir_for(input_path: Path, voice: str, rate: str, pitch: str, max_chars: int) -> Path:
    """A chunk cache keyed by input content + voice settings, so re-running with
    the same choices resumes instantly (and changing a setting starts fresh)."""
    h = hashlib.sha1()
    h.update(input_path.read_bytes())
    h.update(f"|{voice}|{rate}|{pitch}|{max_chars}".encode())
    return CACHE_DIR / h.hexdigest()[:16]


# ----------------------------------------------------------------------------
# Callbacks
# ----------------------------------------------------------------------------


def refilter_voices(locale_filter: str):
    labels = _labels_for(locale_filter)
    value = narrate.DEFAULT_VOICE if locale_filter in ("All", "") else None
    default = _default_label() if (locale_filter in ("All", "")) else (labels[0] if labels else None)
    return gr.update(choices=labels, value=default)


def preview_voice(voice_label: str, rate: int, pitch: int):
    if not voice_label:
        raise gr.Error("Pick a voice first.")
    short = _short_from_label(voice_label)
    tmp = Path(tempfile.mkdtemp(prefix="preview_")) / "sample.mp3"
    try:
        asyncio.run(
            narrate.synthesize_to_file(SAMPLE_TEXT, short, _pct(rate), _hz(pitch), tmp)
        )
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"Preview failed: {exc}")
    return str(tmp)


def reset_defaults():
    return REC_RATE, REC_PITCH


def generate(document, voice_label, rate, pitch, fmt, rules_text, keep_chunks,
             progress=gr.Progress()):
    if not document:
        raise gr.Error("Attach a document first (.docx, .pdf, .txt, or .md).")
    if not voice_label:
        raise gr.Error("Pick a voice first.")

    input_path = Path(document)
    if input_path.suffix.lower() not in narrate.EXTRACTORS:
        raise gr.Error(f"Unsupported file type '{input_path.suffix}'. "
                       f"Use one of: {', '.join(sorted(narrate.EXTRACTORS))}")

    short = _short_from_label(voice_label)
    rate_s, pitch_s = _pct(rate), _hz(pitch)

    # Parse simple "from=to" rules, one per line.
    rules: list[tuple[str, str]] = []
    for line in (rules_text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        src, dst = line.split("=", 1)
        rules.append((src, dst))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{input_path.stem}.{fmt}"
    work_dir = _cache_dir_for(input_path, short, rate_s, pitch_s, narrate.MAX_CHUNK_CHARS)

    progress(0.02, desc="Extracting & cleaning text…")

    def on_chunk(i: int, total: int, chars: int, status: str) -> None:
        progress(i / max(total, 1), desc=f"Synthesizing chunk {i}/{total} ({status})")

    try:
        result = narrate.build_audiobook(
            input_path, output,
            voice=short, rate=rate_s, pitch=pitch_s, fmt=fmt,
            user_rules=rules, work_dir=work_dir, keep_chunks=keep_chunks,
            on_chunk=on_chunk,
        )
    except ValueError as exc:
        raise gr.Error(str(exc))
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"Generation failed: {exc}  (re-run to resume from where it stopped)")

    dur = narrate._fmt_duration(result.duration) if result.duration is not None else "unknown"
    status = (
        f"### ✅ Done\n"
        f"- **Voice:** {short}  &nbsp;•&nbsp; **Rate:** {rate_s}  &nbsp;•&nbsp; **Pitch:** {pitch_s}\n"
        f"- **Format:** {fmt.upper()}  &nbsp;•&nbsp; **Duration:** {dur}  &nbsp;•&nbsp; "
        f"**Size:** {narrate._fmt_size(result.size)}\n"
        f"- **Chunks:** {result.n_chunks}  &nbsp;•&nbsp; **Characters:** {result.total_chars:,}\n"
        f"- **Saved to:** `{result.output}`"
    )
    return str(result.output), str(result.output), status


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Doc2Audiobook") as demo:
        gr.Markdown(
            "# 📖 Doc2Audiobook\n"
            "Turn a **.docx / .pdf / .txt / .md** document into a natural-sounding "
            "narrated audiobook using free neural voices. "
            "Preview a voice before committing, tune pitch & pace, and export MP3/M4A/WAV."
        )

        if _LOAD_ERROR:
            gr.Markdown(
                f"> ⚠️ Could not load the voice list (offline?): `{_LOAD_ERROR}`\n"
                "> You can still type a voice short-name manually below."
            )

        with gr.Row():
            with gr.Column(scale=1):
                document = gr.File(
                    label="Document",
                    file_types=[".docx", ".pdf", ".txt", ".md", ".markdown"],
                    type="filepath",
                )
                locale_filter = gr.Dropdown(
                    label="Language / locale filter",
                    choices=["All", *_locales()],
                    value="All",
                )
                voice = gr.Dropdown(
                    label="Voice",
                    choices=_labels_for("All"),
                    value=_default_label(),
                    allow_custom_value=True,
                )
                with gr.Row():
                    preview_btn = gr.Button("🔊 Preview voice", variant="secondary")
                preview_audio = gr.Audio(label="Voice sample", type="filepath", interactive=False)

                with gr.Accordion("Speech tuning", open=True):
                    rate = gr.Slider(-50, 50, value=REC_RATE, step=1,
                                     label="Pace (rate %)  — negative is slower")
                    pitch = gr.Slider(-50, 50, value=REC_PITCH, step=1,
                                      label="Pitch (Hz)  — 0 is natural")
                    reset_btn = gr.Button("↺ Recommended defaults", size="sm")

                fmt = gr.Radio(FORMATS, value="mp3", label="Output format")
                with gr.Accordion("Advanced", open=False):
                    rules_text = gr.Textbox(
                        label="Find/replace rules for speech (one per line: FROM=TO)",
                        placeholder="NOTE:=Here is an important note.\nTIP:=A quick tip.",
                        lines=4,
                    )
                    keep_chunks = gr.Checkbox(
                        label="Keep intermediate chunk cache (faster re-runs)",
                        value=True,
                    )

                generate_btn = gr.Button("🎧 Generate audiobook", variant="primary", size="lg")

            with gr.Column(scale=1):
                gr.Markdown("### Result")
                result_audio = gr.Audio(label="Narrated audiobook", type="filepath",
                                        interactive=False)
                download = gr.File(label="Download")
                status = gr.Markdown()

        # Wiring
        locale_filter.change(refilter_voices, inputs=locale_filter, outputs=voice)
        preview_btn.click(preview_voice, inputs=[voice, rate, pitch], outputs=preview_audio)
        reset_btn.click(reset_defaults, outputs=[rate, pitch])
        generate_btn.click(
            generate,
            inputs=[document, voice, rate, pitch, fmt, rules_text, keep_chunks],
            outputs=[result_audio, download, status],
        )

        gr.Markdown(
            "<small>Prototype (Phase 1). Engine: `narrate.py` • Voices: edge-tts (free). "
            "See `ROADMAP.md` for paid voices, previews cache, and the native-app path.</small>"
        )
    return demo


def main() -> None:
    build_ui().launch(theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
