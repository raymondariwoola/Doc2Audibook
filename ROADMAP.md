# Doc2Audiobook — Product Roadmap

> **Living document.** This is the north star for where the project is going.
> Update it as we build: check off items, revise decisions, and record *why*
> things changed. Each phase is shippable on its own.

**Vision:** run the app, attach a document, browse voices (free → paid) and
**hear a sample before committing**, tune **pitch & pace** (with recommended
defaults), and export a clean audiobook in the **best format** (MP3 / M4A / M4B
with chapters / WAV). Available as a **local website** and eventually a **native
desktop app for macOS and Windows** — all running locally, not a public service.

---

## Where we are

| Phase | State | Deliverable |
|------:|:-----:|-------------|
| **0. CLI engine** | ✅ Done | `narrate.py` — extract → clean → chunk → edge-tts → ffmpeg |
| **1. Gradio prototype** | ✅ Done | `app.py` — browser UI to *feel* the product |
| **2. Provider layer** | 🔨 In progress | Pluggable free + paid TTS engines (`providers.py`) |
| **3. Local web app** | ⬜ Planned | FastAPI + React, runs on `localhost` |
| **4. Native desktop** | ⬜ Planned | Packaged `.app` (macOS) + `.exe` (Windows) |
| **5. Audiobook polish** | ⬜ Planned | M4B chapters, resume UX, library |

Legend: ✅ done · 🔨 in progress · ⬜ planned

---

## Architecture north star

Keep the **engine** and the **interface** separate so every phase reuses the same
core. UIs are thin; the pipeline is the product.

```
                ┌─────────────────────────────────────────────┐
   Interfaces   │  CLI  ·  Gradio  ·  React web  ·  Desktop    │
                └───────────────────────┬─────────────────────┘
                                        │ calls
                ┌───────────────────────▼─────────────────────┐
   Engine       │  extract → clean → chunk → synthesize → mux  │
   (narrate.py) │  (resumable, progress callbacks)            │
                └───────────────────────┬─────────────────────┘
                                        │ uses
                ┌───────────────────────▼─────────────────────┐
   Providers    │  TTSProvider interface:                     │
                │   list_voices() · preview() · synthesize()  │
                │   capabilities()                            │
                │  ├─ EdgeTTS (free)      ├─ OpenAI (paid)    │
                │  ├─ Piper/Kokoro (local)├─ ElevenLabs (paid)│
                │  └─ Apple say (local)   └─ Azure HD (paid)  │
                └─────────────────────────────────────────────┘
```

**Guiding principles**
- The engine never imports UI code. UIs depend on the engine, never vice-versa.
- Every long operation takes a `progress_cb` so any UI can show progress.
- Providers advertise `capabilities()` (pitch? cloning? sample rate? formats?)
  so the UI can render the right controls instead of hard-coding them.
- Resumability (skip existing chunks) is an engine feature, free to every UI.

---

## Phase 0 — CLI engine ✅

The foundation, already shipped.

- [x] Extract `.docx` (document-order body walk, tables read in place as
      `term. definition.`), `.pdf`, `.txt`, `.md`.
- [x] Speech cleanup: H1 pause cues, verbalize formulas/symbols, strip markdown.
- [x] User find/replace rules (`--replace`, `--rules`).
- [x] Chunk `<3000` chars on paragraph boundaries; **resumable** (skip existing).
- [x] edge-tts synthesis with configurable **rate**.
- [x] Corporate-proxy **SSL fallback** (system trust store).
- [x] ffmpeg stream-copy assembly; prints duration + size.

---

## Phase 1 — Gradio prototype ✅

Goal: make the vision **tangible** fast, on top of the existing engine.

- [x] `app.py` Gradio UI.
- [x] Drag-and-drop document upload (`.docx/.pdf/.txt/.md`).
- [x] Voice picker (edge-tts voices, filterable by locale/gender).
- [x] **Preview button** — synthesize a short sample of the selected voice at the
      current pitch/rate and play it inline before committing.
- [x] **Pitch & pace sliders** with a "Recommended defaults" reset.
- [x] **Output format** choice: MP3 / M4A / WAV.
- [x] Progress bar per chunk; final duration + size; download button.
- [x] Provider picker in the UI (added in Phase 2): switch engines, voices reload
      per provider, pitch control disables where unsupported.

**Engine work folded in this phase**
- [x] Add **pitch** support through the pipeline (`edge-tts` supports `pitch=`).
- [x] Reusable `build_audiobook(...)` core with a `progress_cb`.
- [x] Format transcoding via ffmpeg (mp3 stream-copy; m4a/aac; wav/pcm).

**Known limits to revisit later**
- edge-tts source audio is 24 kHz mono ~48 kbps — WAV/high-bitrate exports
  re-wrap the same audio; true fidelity needs a higher-quality provider (Phase 2).
- Gradio gives limited layout control — fine for a prototype, replaced in Phase 3.

---

## Phase 2 — Provider layer 🔨

Goal: the "free → paid voices with previews" part of the vision. Lives in
[`providers.py`](providers.py); the engine and prototype were **not** disrupted —
edge-tts is the default provider and all existing signatures were preserved.

- [x] Define `TTSProvider` interface: `list_voices()`, `synthesize()`,
      `capabilities()`, `is_available()` (previews reuse `synthesize`).
- [x] `EdgeTTSProvider` — reference implementation, owns the SSL fallback.
- [x] `OpenAIProvider` — **experimental**, key-gated (`OPENAI_API_KEY`); rate maps
      to `speed`, pitch unsupported (advertised via `capabilities()`). Coded to the
      documented API but **not yet verified end-to-end** (no key here).
- [x] Provider registry + `--provider` CLI flag; `available_providers()` reports
      which are usable right now.
- [x] **Free/local — all done:** `PiperProvider` (offline neural), `KokoroProvider`
      (offline Kokoro-82M, higher quality), `AppleSayProvider` (macOS built-in,
      zero downloads). All offline, rate-capable, pitch unsupported (advertised via
      capabilities), tested end-to-end (synthesize → MP3 → assemble → resume).
- [x] **Reproducibility:** `voices.manifest.json` + `sync_voices.py` +
      `bootstrap.py` — one command sets up any machine (deps, ffmpeg check, model
      downloads) so switching machines reaches parity. Agent docs: `AGENTS.md`,
      `CLAUDE.md`, `.github/copilot-instructions.md`.
- [ ] **Paid:** `ElevenLabsProvider`, `AzureProvider`; verify `OpenAIProvider` with a real key.
- [ ] Secure key storage (OS keychain; currently env var / `.env`, never committed).
- [ ] Per-voice **sample cache** so previews are instant after first play.
- [x] Capability-driven UI: provider picker in the prototype — voices reload per
      provider, pitch control disables where unsupported, availability/API-key
      status shown inline.

### Voice provider reference

| Provider | Cost | Strengths | Notes |
|---|---|---|---|
| **edge-tts** ✅ | Free | Natural, 300+ voices, no key | Default. 24 kHz mono ceiling |
| **Kokoro** ✅ | Free/local | Great quality for its size, offline | Implemented. ~340 MB model |
| **Piper** ✅ | Free/local | Fast, fully offline, cross-platform | Implemented. Lower ceiling than neural cloud |
| **Apple say** ✅ | Free/local | Built into macOS, zero downloads | Implemented. macOS only |
| **OpenAI TTS** | Paid | Cheap, *steerable* tone | `gpt-4o-mini-tts` |
| **ElevenLabs** | Paid | Best naturalness + cloning | Limited free tier |
| **Azure HD** | Paid | HD voices, SSML, high bitrate | Same family as edge-tts |
| **Google / Polly** | Paid | Many languages, enterprise | Chirp 3 HD / Generative |

---

## Phase 3 — Local web app ⬜

Goal: the "functioning website, running locally" milestone. Real foundation.

- [ ] **FastAPI** backend wrapping the engine.
  - [ ] `GET /voices`, `POST /preview`, `POST /jobs`, `GET /jobs/{id}/events` (SSE progress).
  - [ ] Background job queue; resumable jobs keyed by content + settings hash.
- [ ] **React** frontend (Vite): drag-drop upload, voice cards with play buttons,
      pitch/pace sliders, format picker, live progress, download/library.
- [ ] One-command local launch (`localhost`), no public exposure.

---

## Phase 4 — Native desktop ⬜

Goal: installable app for macOS **and** Windows.

- [ ] Choose shell: **Tauri** (wrap the Phase 3 web app, small binaries) vs
      **PySide6/Toga** (pure-Python native). *Decision pending — see below.*
- [ ] Bundle Python engine + **ffmpeg** so users install nothing.
- [ ] Native file dialogs, drag-drop, notifications.
- [ ] Packaging + signing: `.app`/`.dmg` (macOS), `.exe`/installer (Windows).
- [ ] Auto-update channel (optional).

**Open decision — desktop shell**
- *Tauri:* reuses the React UI directly, tiny binaries, but adds a Rust toolchain.
- *PySide6:* pure Python (no second UI codebase), but a separate UI from the web app.
- *pywebview:* lightest wrapper around the web app; middle ground.
- **Leaning Tauri** so Phase 3 and Phase 4 share one UI. Revisit after Phase 3.

---

## Phase 5 — Audiobook polish ⬜

- [ ] **M4B export with chapters** derived from Heading-1 breaks (native chapter
      navigation — the real audiobook payoff).
- [ ] Per-book **profiles** (voice + pitch + rate + rules) saved and reusable.
- [ ] Pronunciation lexicon / SSML for names & jargon.
- [ ] Library view: manage generated audiobooks, re-render, resume.
- [ ] Batch mode (a folder of documents).

---

## Decisions & changelog

Record notable choices here so future-us remembers the *why*.

- **2026-07-24** — Roadmap created. Chose incremental path
  **CLI → Gradio → FastAPI+React → native**, keeping a strict engine/UI split so
  each layer reuses `narrate.py`. Gradio picked for the first UI purely for speed
  to a testable prototype; not the long-term UI.
- **2026-07-24** — Confirmed edge-tts supports `pitch`, so pitch/pace controls are
  an engine tweak, not a new provider. Documented its 24 kHz output ceiling as the
  reason higher fidelity waits for Phase 2 providers.
- **2026-07-24** — Started Phase 2. Introduced `providers.py` **additively**: edge
  stays the default and every existing `narrate` signature is unchanged, so the CLI
  and Gradio prototype behave identically (verified). `OpenAIProvider` added as a
  key-gated, capability-aware reference for paid engines but left **unverified**
  pending a real key — deliberately not wired into the prototype UI yet to avoid
  shipping an untested path into the happy flow. Capabilities (`supports_pitch`
  etc.) exist so a future provider picker can adapt controls per engine.
- **2026-07-24** — Added `PiperProvider` (free, fully offline) and the provider
  picker in the prototype UI. Verified `onnxruntime`/`piper-tts` install on Python
  3.14 (cp314 wheels exist) and tested the full offline pipeline end-to-end. Piper
  emits 16–22 kHz WAV, so the provider transcodes each chunk to MP3 (via ffmpeg,
  `-f mp3`) to keep the resumable-chunk + stream-copy assembly uniform across
  providers. Note: on the python.org macOS build, `piper.download_voices` fails TLS
  verification (urllib uses no CA store by default) — documented `curl`/certifi as
  the workaround for fetching models; runtime synthesis is fully offline.
- **2026-07-24** — Completed the free provider set: added `KokoroProvider`
  (`kokoro-onnx`, ~340 MB model, higher quality) and `AppleSayProvider` (macOS
  built-in `say`, zero downloads). Shared `_transcode_wav_to_mp3` helper across all
  local providers. **Cross-machine reproducibility:** added `voices.manifest.json`
  (git-tracked source of truth for models), `sync_voices.py` (idempotent downloader
  using certifi's CA bundle — the clean fix for the urllib TLS issue), and
  `bootstrap.py` (one command: venv + deps + ffmpeg check + model sync, cross-OS).
  Model files stay gitignored and are re-fetched per machine. Added agent docs
  (`AGENTS.md` canonical, `CLAUDE.md` + `.github/copilot-instructions.md` point to
  it) documenting the machine-switch protocol so agents behave consistently.
