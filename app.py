#!/usr/bin/env python3
"""
Doc2Audiobook — Gradio prototype UI (Phase 1 of ROADMAP.md).

A thin browser front-end over the tested `narrate.py` engine: attach a document,
pick a TTS **provider** (Edge, Piper, Kokoro, Apple, or experimental OpenAI),
browse its voices and hear a sample before committing, tune pitch & pace, choose
an output format, and download the narrated audiobook.

Run:
    python app.py            # opens http://127.0.0.1:7860
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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

APP_THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.indigo,
    secondary_hue=gr.themes.colors.cyan,
    neutral_hue=gr.themes.colors.slate,
    spacing_size=gr.themes.sizes.spacing_md,
    radius_size=gr.themes.sizes.radius_md,
)

APP_CSS = """
:root {
    --d2a-ink: #0f172a;
    --d2a-muted: #64748b;
    --d2a-line: #dbe4f0;
    --d2a-panel: #ffffff;
    --d2a-canvas: #f3f6fb;
    --d2a-brand: #4f46e5;
    --d2a-brand-dark: #3730a3;
    --d2a-accent: #06b6d4;
}

.gradio-container {
    background: var(--d2a-canvas) !important;
    color: var(--d2a-ink);
    min-height: 100vh;
}

#app-shell {
    width: min(1240px, calc(100% - 40px));
    margin: 0 auto;
    padding: 28px 0 36px;
}

.app-hero {
    position: relative;
    overflow: hidden;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 32px;
    align-items: end;
    padding: 42px 46px;
    color: #f8fafc;
    background: #111827;
    border: 1px solid #1e293b;
    border-radius: 24px;
    box-shadow: 0 22px 60px rgba(15, 23, 42, 0.18);
}

.app-hero::after {
    content: "";
    position: absolute;
    width: 260px;
    height: 260px;
    right: -92px;
    top: -126px;
    border: 54px solid rgba(6, 182, 212, 0.13);
    border-radius: 50%;
    pointer-events: none;
}

.brand-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 28px;
}

.brand-mark {
    display: grid;
    width: 38px;
    height: 38px;
    place-items: center;
    background: var(--d2a-brand);
    border-radius: 10px;
    color: white;
    font-weight: 800;
    letter-spacing: -0.04em;
}

.brand-name {
    color: #e2e8f0;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.app-hero h1 {
    max-width: 760px;
    margin: 0;
    color: white;
    font-size: clamp(34px, 5vw, 56px);
    font-weight: 760;
    letter-spacing: -0.045em;
    line-height: 1.02;
}

.app-hero p {
    max-width: 690px;
    margin: 20px 0 0;
    color: #aebbd0;
    font-size: 17px;
    line-height: 1.65;
}

.hero-meta {
    position: relative;
    z-index: 1;
    display: grid;
    gap: 10px;
    min-width: 190px;
}

.hero-meta-item {
    padding: 13px 15px;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 12px;
}

.hero-meta-item strong {
    display: block;
    color: white;
    font-size: 14px;
}

.hero-meta-item span {
    color: #94a3b8;
    font-size: 12px;
}

.workspace-grid {
    gap: 24px !important;
    align-items: flex-start !important;
    margin-top: 24px;
}

.surface-card {
    background: var(--d2a-panel) !important;
    border: 1px solid var(--d2a-line) !important;
    border-radius: 20px !important;
    box-shadow: 0 12px 34px rgba(15, 23, 42, 0.07) !important;
    padding: 26px !important;
}

.section-heading {
    margin-bottom: 22px;
}

.section-kicker {
    margin-bottom: 6px;
    color: var(--d2a-brand);
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.1em;
    text-transform: uppercase;
}

.section-heading h2 {
    margin: 0;
    color: var(--d2a-ink);
    font-size: 24px;
    letter-spacing: -0.025em;
}

.section-heading p {
    margin: 7px 0 0;
    color: var(--d2a-muted);
    font-size: 14px;
    line-height: 1.55;
}

.step-label {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 23px 0 11px;
    color: #334155;
    font-size: 13px;
    font-weight: 750;
}

.step-number {
    display: grid;
    width: 24px;
    height: 24px;
    place-items: center;
    background: #eef2ff;
    border: 1px solid #c7d2fe;
    border-radius: 7px;
    color: var(--d2a-brand-dark);
    font-size: 12px;
}

.upload-zone {
    border: 1px dashed #a5b4fc !important;
    border-radius: 14px !important;
    background: #f8faff !important;
}

.provider-note {
    margin-top: 10px !important;
    padding: 13px 15px !important;
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 12px !important;
    color: #475569 !important;
    font-size: 13px !important;
}

.provider-note p {
    margin: 0 0 5px !important;
}

.voice-row,
.provider-row {
    align-items: end !important;
    gap: 12px !important;
}

.compact-audio {
    margin-top: 12px;
}

.tuning-panel,
.advanced-panel {
    margin-top: 18px !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 14px !important;
    background: #fbfcfe !important;
}

.format-switch label {
    font-weight: 700 !important;
}

.format-switch [role="radiogroup"] {
    display: grid !important;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
}

.generate-button {
    min-height: 50px !important;
    margin-top: 22px !important;
    border: 1px solid var(--d2a-brand-dark) !important;
    border-radius: 12px !important;
    background: var(--d2a-brand) !important;
    box-shadow: 0 10px 22px rgba(79, 70, 229, 0.22) !important;
    font-size: 15px !important;
    font-weight: 750 !important;
}

.generate-button:hover {
    background: var(--d2a-brand-dark) !important;
    transform: translateY(-1px);
}

.preview-button,
.reset-button {
    border-radius: 10px !important;
    font-weight: 700 !important;
}

.result-card {
    min-height: 560px;
}

.empty-result {
    display: grid;
    min-height: 190px;
    margin: 10px 0 18px;
    padding: 30px;
    place-items: center;
    text-align: center;
    background: #f8fafc;
    border: 1px dashed #cbd5e1;
    border-radius: 16px;
}

.empty-result-icon {
    display: grid;
    width: 52px;
    height: 52px;
    margin: 0 auto 14px;
    place-items: center;
    color: var(--d2a-brand);
    background: #eef2ff;
    border-radius: 14px;
    font-size: 22px;
    font-weight: 800;
}

.empty-result strong {
    display: block;
    color: #334155;
    font-size: 15px;
}

.empty-result span {
    display: block;
    max-width: 320px;
    margin-top: 7px;
    color: var(--d2a-muted);
    font-size: 13px;
    line-height: 1.5;
}

.result-audio {
    margin-top: 6px;
}

.result-status {
    margin-top: 16px !important;
    padding: 16px 18px !important;
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 14px !important;
}

.benefit-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-top: 22px;
}

.benefit-item {
    padding: 15px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 13px;
}

.benefit-item strong {
    display: block;
    color: #334155;
    font-size: 13px;
}

.benefit-item span {
    display: block;
    margin-top: 5px;
    color: var(--d2a-muted);
    font-size: 12px;
    line-height: 1.45;
}

.app-footer {
    display: flex;
    justify-content: space-between;
    gap: 20px;
    margin-top: 18px;
    padding: 0 4px;
    color: #7c8ba1;
    font-size: 12px;
}

@media (max-width: 900px) {
    #app-shell {
        width: min(100% - 24px, 720px);
        padding-top: 12px;
    }

    .app-hero {
        grid-template-columns: 1fr;
        padding: 30px 26px;
        border-radius: 18px;
    }

    .hero-meta {
        grid-template-columns: repeat(3, 1fr);
        min-width: 0;
    }

    .workspace-grid {
        flex-direction: column !important;
    }

    .workspace-grid > div {
        width: 100% !important;
    }
}

@media (max-width: 600px) {
    .surface-card {
        padding: 18px !important;
        border-radius: 16px !important;
    }

    .app-hero h1 {
        font-size: 34px;
    }

    .hero-meta,
    .benefit-grid {
        grid-template-columns: 1fr;
    }

    .app-footer {
        display: block;
        line-height: 1.7;
    }
}
"""


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


def _render_digest(input_path: Path, provider_id: str, voice: str,
                   rate: str, pitch: str, max_chars: int,
                   rules: list[tuple[str, str]]) -> str:
    """Hash every UI input that can affect the rendered audio."""
    h = hashlib.sha1()
    h.update(input_path.read_bytes())
    settings = {
        "provider": provider_id,
        "provider_identity": providers.get_provider(provider_id).cache_identity(),
        "voice": voice,
        "rate": rate,
        "pitch": pitch,
        "max_chars": max_chars,
        "rules": rules,
    }
    h.update(json.dumps(settings, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:16]


def _cache_dir_for(input_path: Path, provider_id: str, voice: str,
                   rate: str, pitch: str, max_chars: int,
                   rules: list[tuple[str, str]]) -> Path:
    digest = _render_digest(
        input_path, provider_id, voice, rate, pitch, max_chars, rules
    )
    return CACHE_DIR / digest


def _output_path_for(input_path: Path, fmt: str, digest: str) -> Path:
    """Return a collision-safe but still recognizable generated output path."""
    return OUTPUT_DIR / f"{input_path.stem}-{digest[:10]}.{fmt}"


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

    digest = _render_digest(
        input_path, provider_id, short, rate_s, pitch_s,
        narrate.MAX_CHUNK_CHARS, rules,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = _output_path_for(input_path, fmt, digest)
    work_dir = _cache_dir_for(input_path, provider_id, short, rate_s, pitch_s,
                              narrate.MAX_CHUNK_CHARS, rules)

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
        f"### Audiobook ready\n"
        f"- **Provider:** {provider_id}  &nbsp;•&nbsp; **Voice:** {short}\n"
        f"- **Rate:** {rate_s}  &nbsp;•&nbsp; **Pitch:** {pitch_s}  &nbsp;•&nbsp; **Format:** {fmt.upper()}\n"
        f"- **Duration:** {dur}  &nbsp;•&nbsp; **Size:** {narrate._fmt_size(result.size)}  "
        f"&nbsp;•&nbsp; **Chunks:** {result.n_chunks}  &nbsp;•&nbsp; **Chars:** {result.total_chars:,}\n"
        f"- **Saved to:** `{result.output}`"
    )
    return str(result.output), str(result.output), status, gr.update(visible=False)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------


def build_ui() -> gr.Blocks:
    load_provider(DEFAULT_PROVIDER)  # warm the default provider's voice list

    with gr.Blocks(title="Doc2Audiobook", fill_width=True) as demo:
        with gr.Column(elem_id="app-shell"):
            gr.HTML(
                """
                <header class="app-hero">
                  <div>
                    <div class="brand-row">
                      <div class="brand-mark">D</div>
                      <div class="brand-name">Doc2Audiobook</div>
                    </div>
                    <h1>Turn documents into audio worth listening to.</h1>
                    <p>Choose a natural voice, tune the delivery, and produce a
                    polished audiobook locally—without changing your source document.</p>
                  </div>
                  <div class="hero-meta" aria-label="Product highlights">
                    <div class="hero-meta-item"><strong>Private by design</strong><span>Documents stay on this machine</span></div>
                    <div class="hero-meta-item"><strong>Five voice engines</strong><span>Online and fully offline options</span></div>
                    <div class="hero-meta-item"><strong>Resumable renders</strong><span>Continue safely after interruption</span></div>
                  </div>
                </header>
                """
            )

            with gr.Row(elem_classes=["workspace-grid"]):
                with gr.Column(scale=5, min_width=420):
                    with gr.Group(elem_classes=["surface-card"]):
                        gr.HTML(
                            """
                            <div class="section-heading">
                              <div class="section-kicker">Create</div>
                              <h2>Build your audiobook</h2>
                              <p>Configure the source, narrator, and output. You can
                              preview the voice before starting the full render.</p>
                            </div>
                            <div class="step-label"><span class="step-number">1</span> Add your source</div>
                            """
                        )
                        document = gr.File(
                            label="Source document",
                            file_types=[".docx", ".pdf", ".txt", ".md", ".markdown"],
                            type="filepath",
                            elem_classes=["upload-zone"],
                        )

                        gr.HTML(
                            '<div class="step-label"><span class="step-number">2</span> Choose the narrator</div>'
                        )
                        with gr.Row(elem_classes=["provider-row"]):
                            provider = gr.Dropdown(
                                label="Voice provider",
                                choices=_provider_choices(),
                                value=DEFAULT_PROVIDER,
                                scale=3,
                            )
                            locale_filter = gr.Dropdown(
                                label="Language / locale",
                                choices=["All", *_locales(DEFAULT_PROVIDER)],
                                value="All",
                                scale=2,
                            )
                        provider_info = gr.Markdown(
                            _provider_note(DEFAULT_PROVIDER),
                            elem_classes=["provider-note"],
                        )
                        with gr.Row(elem_classes=["voice-row"]):
                            voice = gr.Dropdown(
                                label="Voice",
                                choices=_labels_for(DEFAULT_PROVIDER, "All"),
                                value=_default_label(DEFAULT_PROVIDER),
                                allow_custom_value=True,
                                scale=4,
                            )
                            preview_btn = gr.Button(
                                "Preview voice",
                                variant="secondary",
                                scale=1,
                                elem_classes=["preview-button"],
                            )
                        preview_audio = gr.Audio(
                            label="Voice preview",
                            type="filepath",
                            interactive=False,
                            elem_classes=["compact-audio"],
                        )

                        gr.HTML(
                            '<div class="step-label"><span class="step-number">3</span> Fine-tune the delivery</div>'
                        )
                        with gr.Accordion(
                            "Voice tuning",
                            open=True,
                            elem_classes=["tuning-panel"],
                        ):
                            rate = gr.Slider(
                                -50, 50, value=REC_RATE, step=1,
                                label="Pace (%) — negative is slower",
                            )
                            pitch = gr.Slider(
                                -50, 50, value=REC_PITCH, step=1,
                                label="Pitch (Hz)",
                            )
                            reset_btn = gr.Button(
                                "Use recommended defaults",
                                size="sm",
                                elem_classes=["reset-button"],
                            )

                        gr.HTML(
                            '<div class="step-label"><span class="step-number">4</span> Select the output</div>'
                        )
                        fmt = gr.Radio(
                            FORMATS,
                            value="mp3",
                            label="Audio format",
                            elem_classes=["format-switch"],
                        )
                        with gr.Accordion(
                            "Advanced options",
                            open=False,
                            elem_classes=["advanced-panel"],
                        ):
                            rules_text = gr.Textbox(
                                label="Pronunciation replacements",
                                info="One per line in FROM=TO format.",
                                placeholder="NOTE:=Here is an important note.\nTIP:=A quick tip.",
                                lines=4,
                            )
                            keep_chunks = gr.Checkbox(
                                label="Keep intermediate chunk cache for faster re-runs",
                                value=True,
                            )

                        generate_btn = gr.Button(
                            "Generate audiobook  →",
                            variant="primary",
                            size="lg",
                            elem_classes=["generate-button"],
                        )

                with gr.Column(scale=6, min_width=440):
                    with gr.Group(elem_classes=["surface-card", "result-card"]):
                        gr.HTML(
                            """
                            <div class="section-heading">
                              <div class="section-kicker">Output</div>
                              <h2>Listen and download</h2>
                              <p>Your completed audiobook will appear here with its
                              duration, file size, and render details.</p>
                            </div>
                            """
                        )
                        result_empty = gr.HTML(
                            """
                            <div class="empty-result">
                              <div>
                                <div class="empty-result-icon">♪</div>
                                <strong>Your audiobook is ready when you are</strong>
                                <span>Add a document and preview a voice, then generate
                                the full narration.</span>
                              </div>
                            </div>
                            """
                        )
                        result_audio = gr.Audio(
                            label="Audiobook player",
                            type="filepath",
                            interactive=False,
                            elem_classes=["result-audio"],
                        )
                        download = gr.File(label="Download audiobook")
                        status = gr.Markdown(elem_classes=["result-status"])
                        gr.HTML(
                            """
                            <div class="benefit-grid">
                              <div class="benefit-item"><strong>Safe resume</strong><span>Matching chunks are reused after interruption.</span></div>
                              <div class="benefit-item"><strong>Local workflow</strong><span>Source files and output stay on your machine.</span></div>
                              <div class="benefit-item"><strong>Flexible export</strong><span>Choose MP3, M4A, or lossless WAV.</span></div>
                            </div>
                            """
                        )

            gr.HTML(
                """
                <footer class="app-footer">
                  <span>Doc2Audiobook · Local narration workspace</span>
                  <span>Edge · Piper · Kokoro · Apple · OpenAI</span>
                </footer>
                """
            )

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
            outputs=[result_audio, download, status, result_empty],
        )
    return demo


def main() -> None:
    build_ui().launch(theme=APP_THEME, css=APP_CSS)


if __name__ == "__main__":
    main()
