# Contributing to ClipCraft

Thanks for wanting to help. This is a small local-first tool, so the bar is simple: keep it running on one machine, keep it free, don't break the no-secrets-in-git rule.

## Ground rules

- **Localhost only.** The server is deliberately bound to `127.0.0.1` with CSRF-style origin checks in `server.py`. Don't add anything that phones home, uploads user videos to third parties, or auto-posts to social accounts.
- **No secrets in the repo.** API keys live in `settings.json` (gitignored) or env vars. `settings.example.json` is the template — update it if you add a new setting, with an empty value.
- **Heuristics must keep working.** Any LLM-powered feature needs a no-key fallback path. A bunch of users will run this with zero API keys.
- **Match the existing style.** Plain Python, no heavy frameworks beyond Flask + ffmpeg + faster-whisper, compact functions, minimal comments.

## Dev setup

Same as the README: venv, `pip install -r requirements.txt`, `python server.py`. Jobs, downloads, and renders all land in `jobs/`, `downloads/`, `outputs/` — safe to delete between runs.

## Where to look

- `core/virality_engine.py` — moment scoring. The heart of the app.
- `core/pipeline.py` — job stages from ingest to render.
- `core/llm.py` — provider abstraction if you're adding an AI backend.
- `static/app.js` + `templates/index.html` — the UI is vanilla JS, no build step.

## Pull requests

- One focused change per PR.
- Say what you tested: which video/host, which settings, what the output looked like.
- If you touched the pipeline, note whether you ran a real end-to-end job.

## Issues

Bug reports are most useful with the job ID, the source URL or file, and `server.err.log` if anything crashed. Feature ideas welcome — especially around moment quality, since that's the thing everyone argues about.
