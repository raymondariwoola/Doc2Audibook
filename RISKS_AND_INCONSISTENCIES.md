# Risks and inconsistencies

This is the working remediation ledger for Doc2Audiobook. Update a row when its
status changes and keep the supporting product and operator documentation in
sync. `ROADMAP.md` remains the product plan; this file records concrete failure
modes and the decision made for each one.

Status: **resolved** means the repository contains the mitigation and its
relevant automated checks pass. **Controlled** means the limitation is
intentional, documented, and has a safe operating boundary. **Open** means it
must not be represented as complete.

| ID | Risk or inconsistency | Impact | Resolution / control | Status |
|---|---|---|---|---|
| R-001 | Phase 1 documentation said Gradio would be “replaced” by Phase 3, while the prototype is still useful for comparison and recovery. | A later change could delete `app.py`, its entry point, or its setup path. | Treat CLI, Gradio, and React as additive interfaces. Keep `python app.py` / `narrate-ui` supported and regression-tested after Phase 3 changes. | Resolved |
| R-002 | A generic cloud-oriented web scaffold conflicted with the roadmap’s local React/Vite architecture. | Unneeded Worker, database, and framework layers add build failures and blur the local-only security model. | Use a plain React/Vite frontend in `web/`; keep cloud deployment outside Phase 3. | Resolved |
| R-003 | Two browser interfaces could collide on ports, generated files, or cache state. | One UI could mask another process or reuse incompatible work. | Gradio uses `127.0.0.1:7860+`; the Phase 3 app uses `127.0.0.1:8000`; web jobs and previews use dedicated subdirectories. | Resolved |
| R-004 | Local-provider singleton models are not documented as thread-safe. | Parallel Piper/Kokoro calls could corrupt state or fail nondeterministically. | Serialize web generation and preview synthesis behind one process-local lock for the initial web release. | Resolved |
| R-005 | User filenames and HTTP input cross a filesystem boundary. | Path traversal, oversized uploads, invalid settings, accidental overwrite, or local-path disclosure. | Store uploads under generated/hash IDs; bound and validate every setting/upload/rule; sanitize client errors while logging full failures server-side. | Resolved |
| R-006 | A local server can still be exposed accidentally or reached through an untrusted host/origin. | API keys and local documents could be reachable outside the machine. | Bind the launcher to loopback only, reject non-loopback CLI hosts, enforce trusted loopback Host headers, and reject unapproved browser Origins before reading POST bodies. | Resolved |
| R-007 | The original bootstrap installed only the CLI, Gradio, and local providers. | A fresh clone would not reproduce the Phase 3 app. | Default bootstrap installs pinned web Python dependencies and runs `npm ci` plus the frontend build; `--no-web` and `--minimal` preserve smaller setups. | Resolved |
| R-008 | Web job status/tasks are process-local even though audio/chunks are on disk. | A restart loses status and cannot automatically reattach the UI; a large burst can also create an unbounded set of waiting tasks. | Synthesis is serialized, chunk work is retained, and identical submissions are content-addressed; replace the task registry with a bounded, durable job manager before Phase 3 is called complete. | Open |
| R-009 | SSE sends snapshots but does not yet provide durable event IDs/heartbeat semantics. | Long proxies or reconnects may require a status refresh and could miss intermediate progress. | The client always fetches current job status and SSE replays the latest snapshot; add event IDs and heartbeats in the next reliability increment. | Open |
| R-010 | The OpenAI provider is implemented without a verified real-key render. | Paid-provider behavior could differ from the documented implementation. | Keep it labelled experimental/key-gated and do not claim end-to-end verification until a real-key test is run. | Controlled |
| R-011 | The generated scaffold pinned a Vite release affected by Windows dev-server path and credential-disclosure advisories. | A malicious request to a developer-only server could disclose files or Windows credentials. | Pin Vite 8.1.5, keep Vite loopback-only, and require a clean `npm audit` in the frontend gate. | Resolved |
| R-012 | The source-tree launcher serves `web/dist/`, but a standalone Python wheel does not yet bundle those static assets. | `narrate-web` from an unpacked wheel could expose the API without the React page. | Treat source/bootstrap launch as the Phase 3 target; add a wheel asset-copy/build hook before desktop packaging or PyPI distribution. | Open |
| R-013 | “Runs locally” can be misread as “all narration stays offline,” although Edge and OpenAI are network providers. | A user could send sensitive document text to a third-party TTS service unintentionally. | Describe the server/UI as local, label provider connectivity clearly, and direct sensitive documents to Piper, Kokoro, or Apple `say`. | Controlled |
| R-014 | FastAPI’s current test client emits Starlette’s `httpx` deprecation warning. | A future dependency upgrade may require a test-client migration even though runtime HTTP behavior is unaffected today. | Keep the direct test dependency pinned and migrate the API tests to Starlette’s recommended successor before upgrading FastAPI/Starlette. | Open |
| R-015 | `ffmpeg` remains an external executable and may be installed without being present on the launching shell’s `PATH`. | Preview can work while final assembly fails at the end of a job. | Bootstrap checks every machine and prints an OS-specific warning; launch from a shell where `ffmpeg -version` succeeds. Bundling is deferred to Phase 4. | Controlled |

## Phase 3 coexistence contract

- `narrate.py` and `providers.py` are shared reusable layers and never import an
  interface.
- `app.py` remains the Gradio prototype. Phase 3 must not rename it, proxy
  through it, or reuse its UI-specific cache keys.
- `web_api/` is the HTTP adapter and `web/` is the React client.
- The built Phase 3 client and API share `http://127.0.0.1:8000`. During
  frontend development, Vite runs separately and proxies relative `/api`
  requests to that address.
- No Phase 3 milestone may be marked complete unless the existing CLI and
  Gradio tests still pass alongside the API and frontend checks.
