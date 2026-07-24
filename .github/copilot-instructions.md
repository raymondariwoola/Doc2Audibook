# GitHub Copilot instructions

**See [../AGENTS.md](../AGENTS.md) for the full guide.** This is a short summary.

## Project

`Doc2Audiobook` turns documents (`.docx/.pdf/.txt/.md`) into narrated audiobooks
via neural TTS. Three separated layers: engine (`narrate.py`), providers
(`providers.py`, a `TTSProvider` registry with `edge` as default), and interfaces
(the CLI in `narrate.py` and the Gradio UI `app.py`). Roadmap in `ROADMAP.md`.

## Switching machines / fresh clone → one command

```bash
python bootstrap.py
```

Idempotent. Creates `.venv`, installs deps, checks `ffmpeg`, and downloads the
offline voice models listed in `voices.manifest.json` (via `sync_voices.py`).
Run it after every `git pull`. Voice models and generated audio are gitignored
and re-fetched/regenerated per machine — never commit them.

## Conventions

- Use the `.venv` interpreter for all commands.
- Keep `edge` the default provider; don't break `narrate.py`'s public function
  signatures (the CLI and UI depend on them). Add providers by subclassing
  `TTSProvider` and registering in `_REGISTRY`.
- Don't commit/push unless asked. Never commit `.venv/`, `piper_voices/`,
  `kokoro_models/`, `outputs/`, or generated audio.
- Keep `ROADMAP.md` updated. Flag untested code (the `openai` provider is unverified).
