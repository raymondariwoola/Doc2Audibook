#!/usr/bin/env python3
"""
Doc2Audiobook — Gradio prototype UI (Phase 1 of ROADMAP.md).

A thin browser front-end over the tested `narrate.py` engine: attach a document,
pick a TTS **provider** (free edge-tts, offline Piper, or paid OpenAI), browse its
voices and hear a sample before committing, tune pitch & pace, choose an output
format, and download the narrated audiobook.

Run:
    python app.py            # opens http://127.0.0.1:7860
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

import gradio as gr

import narrate
import providers

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
DEFAULT_PROVIDER = providers.DEFAULT_PROVIDER_ID


# ----------------------------------------------------------------------------
# Per-provider voice catalog (loaded lazily and cached)
# ----------------------------------------------------------------------------

_CACHE: dict[str, dict] = {}   # provider_id -> {"rows", "label_to_id", "error"}


def _voice_label(v: dict) -> str:
    gender = v.get("Gender") or "—"
    locale = v.get("Locale") or "—"
    return f"{v.get('ShortName', '?')}  ·  {gender}, {locale}"


def load_provider(provider_id: str) -> dict:
    if provider_id in _CACHE:
        return _CACHE[provider_id]
    entry = {"rows": [], "label_to_id": {}, "error": None}
    try:
        rows = asyncio.run(narrate.fetch_voices(provider_id))
        rows.sort(key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
        entry["rows"] = rows
        entry["label_to_id"] = {_voice_label(v): v.get("ShortName", "") for v in rows}
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)
    _CACHE[provider_id] = entry
    return entry


def _locales(provider_id: str) -> list[str]:
    rows = load_provider(provider_id)["rows"]
    return sorted({v.get("Locale", "") for v in rows if v.get("Locale")})


def _labels_for(provider_id: str, locale_filter: str) -> list[str]:
    rows = load_provider(provider_id)["rows"]
    if locale_filter and locale_filter != "All":
        rows = [v for v in rows if v.get("Locale") == locale_filter]
    return [_voice_label(v) for v in rows]


def _default_label(provider_id: str) -> str | None:
    entry = load_provider(provider_id)
    for label, short in entry["label_to_id"].items():
        if short == narrate.DEFAULT_VOICE:
            return label
    labels = list(entry["label_to_id"])
    return labels[0] if labels else None


def _short_from_label(provider_id: str, label: str) -> str:
    entry = load_provider(provider_id)
    return entry["label_to_id"].get(label) or (label.split()[0] if label else "")


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def _pct(v: int) -> str:
    return f"{int(v):+d}%"


def _hz(v: int) -> str:
    return f"{int(v):+d}Hz"


def _provider_choices() -> list[tuple[str, str]]:
    """(display, value) tuples for the provider dropdown, annotated with status."""
    out: list[tuple[str, str]] = []
    for pid in providers.all_provider_ids():
        prov = providers.get_provider(pid)
        if prov.is_available():
            mark = "ready"
        elif prov.capabilities().needs_api_key:
            mark = "needs API key"
        else:
            mark = "unavailable"
        out.append((f"{prov.label}  [{mark}]", pid))
    return out


def _provider_note(provider_id: str) -> str:
    prov = providers.get_provider(provider_id)
    caps = prov.capabilities()
    entry = load_provider(provider_id)

    flags = []
    flags.append("rate ✓" if caps.supports_rate else "rate ✗")
    flags.append("pitch ✓" if caps.supports_pitch else "pitch ✗")
    if caps.needs_api_key:
        flags.append("needs API key")
    lines = [f"**{prov.label}** — {caps.notes}", " · ".join(flags)]

    if entry["error"]:
        lines.append(f"⚠️ Could not load voices: `{entry['error']}`")
    elif not entry["rows"]:
        if provider_id == "piper":
            lines.append(
                "⚠️ No Piper models found. Download one (offline afterwards):\n\n"
                "`python -m piper.download_voices en_US-lessac-medium "
                "--download-dir piper_voices`"
            )
        elif caps.needs_api_key:
            lines.append("⚠️ Set the required API key in your environment, then restart.")
        else:
            lines.append("⚠️ No voices available.")
    return "\n\n".join(lines)


def _cache_dir_for(input_path: Path, provider_id: str, voice: str,
                   rate: str, pitch: str, max_chars: int) -> Path:
    """Chunk cache keyed by content + provider + voice settings, so re-running
    with the same choices resumes instantly (any change starts fresh)."""
    h = hashlib.sha1()
    h.update(input_path.read_bytes())
    h.update(f"|{provider_id}|{voice}|{rate}|{pitch}|{max_chars}".encode())
    return CACHE_DIR / h.hexdigest()[:16]


# ----------------------------------------------------------------------------
# Callbacks
# ----------------------------------------------------------------------------


def on_provider_change(provider_id: str, pitch: int):
    """Reload voices for the chosen provider and adapt controls to its caps."""
    caps = providers.get_provider(provider_id).capabilities()
    locales = ["All", *_locales(provider_id)]
    labels = _labels_for(provider_id, "All")
    default = _default_label(provider_id)
    pitch_on = caps.supports_pitch
    return (
        gr.update(choices=locales, value="All"),                       # locale_filter
        gr.update(choices=labels, value=default),                      # voice
        gr.update(interactive=pitch_on, value=(pitch if pitch_on else 0),
                  label="Pitch (Hz)" + ("" if pitch_on else " — not supported by this provider")),
        _provider_note(provider_id),                                   # provider_info
    )


def refilter_voices(provider_id: str, locale_filter: str):
    labels = _labels_for(provider_id, locale_filter)
    if locale_filter in ("All", ""):
        default = _default_label(provider_id)
    else:
        default = labels[0] if labels else None
    if default not in labels:
        default = labels[0] if labels else None
    return gr.update(choices=labels, value=default)


def preview_voice(provider_id: str, voice_label: str, rate: int, pitch: int):
    if not voice_label:
        raise gr.Error("Pick a voice first.")
    prov = providers.get_provider(provider_id)
    caps = prov.capabilities()
    short = _short_from_label(provider_id, voice_label)
    pitch_s = _hz(pitch) if caps.supports_pitch else "+0Hz"
    tmp = Path(tempfile.mkdtemp(prefix="preview_")) / "sample.mp3"
    try:
        asyncio.run(narrate.synthesize_to_file(
            SAMPLE_TEXT, short, _pct(rate), pitch_s, tmp, provider=prov))
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"Preview failed: {exc}")
    return str(tmp)


def reset_defaults():
    return REC_RATE, REC_PITCH


def generate(document, provider_id, voice_label, rate, pitch, fmt, rules_text,
             keep_chunks, progress=gr.Progress()):
    if not document:
        raise gr.Error("Attach a document first (.docx, .pdf, .txt, or .md).")
    if not voice_label:
        raise gr.Error("Pick a voice first.")

    input_path = Path(document)
    if input_path.suffix.lower() not in narrate.EXTRACTORS:
        raise gr.Error(f"Unsupported file type '{input_path.suffix}'. "
                       f"Use one of: {', '.join(sorted(narrate.EXTRACTORS))}")

    caps = providers.get_provider(provider_id).capabilities()
    short = _short_from_label(provider_id, voice_label)
    rate_s = _pct(rate)
    pitch_s = _hz(pitch) if caps.supports_pitch else "+0Hz"

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
    work_dir = _cache_dir_for(input_path, provider_id, short, rate_s, pitch_s,
                              narrate.MAX_CHUNK_CHARS)

    progress(0.02, desc="Extracting & cleaning text…")

    def on_chunk(i: int, total: int, chars: int, status: str) -> None:
        progress(i / max(total, 1), desc=f"Synthesizing chunk {i}/{total} ({status})")

    try:
        result = narrate.build_audiobook(
            input_path, output,
            voice=short, rate=rate_s, pitch=pitch_s, fmt=fmt, provider=provider_id,
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
        f"- **Provider:** {provider_id}  &nbsp;•&nbsp; **Voice:** {short}\n"
        f"- **Rate:** {rate_s}  &nbsp;•&nbsp; **Pitch:** {pitch_s}  &nbsp;•&nbsp; **Format:** {fmt.upper()}\n"
        f"- **Duration:** {dur}  &nbsp;•&nbsp; **Size:** {narrate._fmt_size(result.size)}  "
        f"&nbsp;•&nbsp; **Chunks:** {result.n_chunks}  &nbsp;•&nbsp; **Chars:** {result.total_chars:,}\n"
        f"- **Saved to:** `{result.output}`"
    )
    return str(result.output), str(result.output), status


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------


def build_ui() -> gr.Blocks:
    load_provider(DEFAULT_PROVIDER)  # warm the default provider's voice list

    with gr.Blocks(title="Doc2Audiobook") as demo:
        gr.Markdown(
            "# 📖 Doc2Audiobook\n"
            "Turn a **.docx / .pdf / .txt / .md** document into a natural-sounding "
            "narrated audiobook. Pick a **provider**, preview a voice before committing, "
            "tune pitch & pace, and export MP3/M4A/WAV."
        )

        with gr.Row():
            with gr.Column(scale=1):
                document = gr.File(
                    label="Document",
                    file_types=[".docx", ".pdf", ".txt", ".md", ".markdown"],
                    type="filepath",
                )
                provider = gr.Dropdown(
                    label="TTS provider",
                    choices=_provider_choices(),
                    value=DEFAULT_PROVIDER,
                )
                provider_info = gr.Markdown(_provider_note(DEFAULT_PROVIDER))
                locale_filter = gr.Dropdown(
                    label="Language / locale filter",
                    choices=["All", *_locales(DEFAULT_PROVIDER)],
                    value="All",
                )
                voice = gr.Dropdown(
                    label="Voice",
                    choices=_labels_for(DEFAULT_PROVIDER, "All"),
                    value=_default_label(DEFAULT_PROVIDER),
                    allow_custom_value=True,
                )
                preview_btn = gr.Button("🔊 Preview voice", variant="secondary")
                preview_audio = gr.Audio(label="Voice sample", type="filepath", interactive=False)

                with gr.Accordion("Speech tuning", open=True):
                    rate = gr.Slider(-50, 50, value=REC_RATE, step=1,
                                     label="Pace (rate %)  — negative is slower")
                    pitch = gr.Slider(-50, 50, value=REC_PITCH, step=1,
                                      label="Pitch (Hz)")
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
        provider.change(
            on_provider_change,
            inputs=[provider, pitch],
            outputs=[locale_filter, voice, pitch, provider_info],
        )
        locale_filter.change(refilter_voices, inputs=[provider, locale_filter], outputs=voice)
        preview_btn.click(preview_voice, inputs=[provider, voice, rate, pitch], outputs=preview_audio)
        reset_btn.click(reset_defaults, outputs=[rate, pitch])
        generate_btn.click(
            generate,
            inputs=[document, provider, voice, rate, pitch, fmt, rules_text, keep_chunks],
            outputs=[result_audio, download, status],
        )

        gr.Markdown(
            "<small>Prototype (Phase 1). Engine: `narrate.py` • Providers: `providers.py` "
            "(edge-tts free · Piper offline · OpenAI paid). See `ROADMAP.md`.</small>"
        )
    return demo


def main() -> None:
    build_ui().launch(theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
