# AGENTS.md — canonical guide for AI coding agents

This is the shared brief for any AI agent (Claude Code, GitHub Copilot, etc.)
working in this repo. `CLAUDE.md` and `.github/copilot-instructions.md` point
here. Keep this file up to date when workflows change.

## What this project is

`Doc2Audiobook` converts a document (`.docx/.pdf/.txt/.md`) into a narrated
audiobook MP3/M4A/WAV using neural text-to-speech. It has three layers, kept
strictly separated (interfaces depend on the engine, never the reverse):

- **Engine** — [`narrate.py`](narrate.py): extract → clean → chunk → synthesize →
  assemble (ffmpeg). Resumable (skips existing chunk MP3s). Reusable
  `build_audiobook(...)` core with progress callbacks.
- **Providers** — [`providers.py`](providers.py): a `TTSProvider` interface
  (`list_voices`, `synthesize`, `capabilities`, `is_available`) with a registry.
  `edge` is the default. Never break existing `narrate` signatures — the CLI and
  UI depend on them.
- **Interfaces** — [`narrate.py`](narrate.py) CLI and the Gradio UI
  [`app.py`](app.py). The long-term plan (web app, desktop) is in
  [`ROADMAP.md`](ROADMAP.md) — read it before large changes and update it as you go.

## ⭐ Switching machines / fresh clone — the ONE command

Whenever you (or the user) move to another machine — e.g. the Windows box "The
Beast" — or clone fresh, get to full parity with:

```bash
python bootstrap.py
```

Idempotent; safe to re-run after every `git pull`. It: creates `.venv`, installs
pinned deps (core + UI + local providers), checks `ffmpeg` (prints install hints
if missing), and downloads the offline voice models in
[`voices.manifest.json`](voices.manifest.json) via [`sync_voices.py`](sync_voices.py),
verifying every model against its recorded SHA-256 digest. It also repairs an
interrupted `.venv` whose Python exists but whose pip installation is incomplete.
Works on macOS/Windows/Linux (pure-stdlib bootstrap, cross-platform venv paths).

**What transfers via git and what doesn't:**

| Transfers via git | Machine-local (regenerated / re-fetched) |
|---|---|
| All code, `ROADMAP.md`, this file, `voices.manifest.json` | `.venv/` (rebuilt by bootstrap) |
| Which voices you use (the manifest) | `piper_voices/`, `kokoro_models/` (re-downloaded by bootstrap) |
| | `outputs/`, `*_chunks/`, `.narrate_cache/` (regenerated on demand) |

So "pick up where we left off" = code/roadmap via git + `python bootstrap.py` to
restore the environment and identical voice set. In-progress *renders* and the
resumable chunk cache are per-machine and not synced (gitignored, cheap to redo).

**Adding a voice for all machines:** edit `voices.manifest.json`, commit, then run
`python sync_voices.py` on each machine. Do **not** commit the model files — they
are large and gitignored on purpose.

## Providers (current)

| id | Cost | Offline | Notes |
|---|---|---|---|
| `edge` (default) | free | no | edge-tts neural voices, no key |
| `piper` | free | yes | needs `piper-tts` + model in `piper_voices/` |
| `kokoro` | free | yes | needs `kokoro-onnx` + model in `kokoro_models/` |
| `apple` | free | yes | macOS built-in `say`; `is_available()` false off-macOS |
| `openai` | paid | no | experimental, key-gated (`OPENAI_API_KEY`), unverified |

Providers advertise `capabilities()` (rate/pitch support, needs_api_key). The UI
and CLI adapt to these — e.g. the pitch control disables where unsupported. Add a
provider by subclassing `TTSProvider` and registering it in `_REGISTRY`.

## Conventions & guardrails

- **Environment:** always use `.venv` (created by bootstrap). Run tools as
  `.venv/bin/python …` (macOS/Linux) or `.venv\Scripts\python …` (Windows).
- **Don't disrupt working layers.** Changes to providers must keep `edge` the
  default and preserve `narrate`'s public function signatures. Verify the CLI and
  the `app.py` callbacks still work after provider changes.
- **Never commit** `.venv/`, voice models, or generated audio (see `.gitignore`).
- **Cache safety:** `narrate.py` fingerprints cleaned chunks plus provider/model,
  voice, pitch, rate, and chunk size. Keep that identity current when adding any
  new audio-affecting option. UI render digests must additionally include every
  input that changes output, including replacement rules.
- **Git:** the user often commits/pushes manually — do not commit or push unless
  explicitly asked. Branch before committing to `main` if you are asked to commit.
- **Honesty about testing:** the `openai` provider is implemented but unverified
  (no key). Mark anything untested as such; don't claim a path works unless you
  ran it.
- **Keep [`ROADMAP.md`](ROADMAP.md) current** — check off items and add a dated
  note in its decisions/changelog when you finish meaningful work.

## Quick test commands

```bash
.venv/bin/python -m py_compile narrate.py providers.py app.py sync_voices.py bootstrap.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python narrate.py --list-voices en-US
.venv/bin/python narrate.py sample.docx --provider piper --voice en_US-lessac-medium
.venv/bin/python -c "import providers as P; print([p.id for p in P.available_providers()])"
```
