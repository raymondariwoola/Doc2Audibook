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
| **1. Gradio prototype** | 🔨 In progress | `app.py` — browser UI to *feel* the product |
| **2. Provider layer** | ⬜ Planned | Pluggable free + paid TTS engines |
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

## Phase 1 — Gradio prototype 🔨

Goal: make the vision **tangible** fast, on top of the existing engine.

- [ ] `app.py` Gradio UI.
- [ ] Drag-and-drop document upload (`.docx/.pdf/.txt/.md`).
- [ ] Voice picker (edge-tts voices, filterable by locale/gender).
- [ ] **Preview button** — synthesize a short sample of the selected voice at the
      current pitch/rate and play it inline before committing.
- [ ] **Pitch & pace sliders** with a "Recommended defaults" reset.
- [ ] **Output format** choice: MP3 / M4A / WAV.
- [ ] Progress bar per chunk; final duration + size; download button.

**Engine work folded in this phase**
- [ ] Add **pitch** support through the pipeline (`edge-tts` supports `pitch=`).
- [ ] Reusable `build_audiobook(...)` core with a `progress_cb`.
- [ ] Format transcoding via ffmpeg (mp3 stream-copy; m4a/aac; wav/pcm).

**Known limits to revisit later**
- edge-tts source audio is 24 kHz mono ~48 kbps — WAV/high-bitrate exports
  re-wrap the same audio; true fidelity needs a higher-quality provider (Phase 2).
- Gradio gives limited layout control — fine for a prototype, replaced in Phase 3.

---

## Phase 2 — Provider layer ⬜

Goal: the "free → paid voices with previews" part of the vision.

- [ ] Define `TTSProvider` interface: `list_voices()`, `preview(voice, opts)`,
      `synthesize(text, voice, opts) -> audio`, `capabilities()`.
- [ ] `EdgeTTSProvider` (wrap current code as the reference implementation).
- [ ] **Free/local:** `PiperProvider`, `KokoroProvider`, `AppleSayProvider`.
- [ ] **Paid (API key):** `OpenAIProvider`, `ElevenLabsProvider`, `AzureProvider`.
- [ ] Secure key storage (OS keychain / `.env`, never committed).
- [ ] Per-voice **sample cache** so previews are instant after first play.
- [ ] Capability-driven UI (hide pitch where unsupported, show cloning where it is).

### Voice provider reference

| Provider | Cost | Strengths | Notes |
|---|---|---|---|
| **edge-tts** | Free | Natural, 300+ voices, no key | 24 kHz mono ceiling |
| **Kokoro** | Free/local | Great quality for its size, offline | Small model, Apache-licensed |
| **Piper** | Free/local | Fast, fully offline | Lower ceiling than neural cloud |
| **Apple say** | Free/local | Built into macOS | Premium voices only-OK |
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
