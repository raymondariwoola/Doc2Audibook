# Doc2Audiobook Phase 3 client

This directory contains the plain React/Vite client for the local FastAPI app.
It is deliberately separate from the retained Gradio prototype in `../app.py`;
both call the same engine and provider layers.

## Development

Start the API from the repository root:

```bash
.venv/bin/python -m web_api
```

Then start Vite in another terminal:

```bash
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Relative `/api` requests are proxied to
`http://127.0.0.1:8000`.

## Local production build

```bash
npm run build
cd ..
.venv/bin/python -m web_api
```

FastAPI serves `web/dist/` and the API together at
`http://127.0.0.1:8000`. The server is intentionally loopback-only.

## Checks

```bash
npm run lint
npm run build
```

Do not add provider credentials to frontend code or `.env` variables beginning
with `VITE_`; browser-visible variables are not secret. Provider keys remain in
the Python process environment.
