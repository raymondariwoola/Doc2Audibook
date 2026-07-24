# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first** — it is the canonical guide (architecture,
providers, conventions, and the machine-switch workflow). This file adds only
Claude Code-specific notes.

## The one thing to remember on a new machine

Fresh clone or switching machines (e.g. to the Windows box "The Beast"):

```bash
python bootstrap.py
```

This restores full parity: `.venv`, dependencies, `ffmpeg` check, and the offline
voice models from [`voices.manifest.json`](voices.manifest.json). Re-run it after
any `git pull`. Details and the "what syncs vs. what's local" table are in
[AGENTS.md](AGENTS.md).

## Working here

- Use the venv: `.venv/bin/python …` (macOS/Linux), `.venv\Scripts\python …` (Windows).
- Keep `edge` the default provider and preserve `narrate.py`'s public signatures;
  after any provider change, verify the CLI **and** `app.py` callbacks still work.
- Do **not** commit or push unless the user explicitly asks — they usually handle
  git themselves. Never commit `.venv/`, voice models, or generated audio.
- Update [ROADMAP.md](ROADMAP.md) (check boxes + dated changelog note) when you
  finish meaningful work.
- Be explicit about what is tested vs. not (the `openai` provider is unverified).
