# ClipCraft

A free, local AI clipping studio. Paste a YouTube link (or any supported video URL), or drop in a file, and it finds the moments worth posting — then renders vertical 9:16 clips with captions, titles, and ready-to-paste social copy. Think of it as a self-hosted take on the paid AI clippers, running entirely on your own machine.

Everything stays on `127.0.0.1`. Nothing gets uploaded anywhere except the video download itself and whatever AI API you choose to configure (or nothing at all — the built-in heuristics work fine without a key).

## What it does

- **Finds real moments, not chunks.** It transcribes the video with word-level timestamps, groups speech into sentences, and scores candidates on hook strength, whether the moment stands on its own, and how it lands as a short. Audio energy spikes and visual motion help when there's little talking.
- **Renders proper vertical clips.** 1080x1920 MP4s with burned-in captions (bold / clean / minimal styles), optional face-aware cropping for talking heads, and gameplay-friendly layouts.
- **Gives you the package.** Each clip comes with a virality score, a title, a short "why this works" note, a preview, a download link, and copy-paste captions/titles for socials.
- **Lets you tweak before you commit.** Preview frames render fast, you can trim in/out points, then re-render just that clip.
- **Plugs into free AI, optionally.** Gemini, or any OpenAI-compatible endpoint (Groq, OpenRouter, Ollama, LM Studio, a local server). A chain of fallback providers is supported. No key? Heuristics carry the whole thing.
- **Handles long videos.** Whisper transcription and LLM scoring chunk large files automatically.

## Requirements

- Python 3.10+
- A few GB of disk for models and renders
- (Optional) an API key for a supported LLM provider

## Setup

```bash
git clone https://github.com/<you>/clipcraft.git
cd clipcraft
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

Then either:

```bash
cp settings.example.json settings.json   # fill in what you want
python server.py
```

or skip the file entirely and use environment variables:

```bash
set GEMINI_API_KEY=your-key-here         # Windows
# export GEMINI_API_KEY=your-key-here    # macOS/Linux
python server.py
```

Open http://127.0.0.1:5003 and you're in.

## Settings

`settings.json` is gitignored on purpose — it holds your API keys. A template ships as `settings.example.json`. The important knobs:

| Key | What it does |
| --- | --- |
| `llm_provider` | `gemini`, `openai_compatible`, or `none` |
| `gemini_api_key` / `openai_api_key` | Your keys (env vars work too) |
| `openai_base_url` / `openai_model` | Any OpenAI-compatible endpoint |
| `fallback_providers` | JSON list of extra engines, tried in order. Edited in `settings.json` only — never sent to the browser |
| `whisper_model` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `cookies_from_browser` | `chrome`, `edge`, `firefox`, etc. — for when YouTube asks "are you a bot?" |
| `brand_name` | Watermark/branding text for clips |

Everything can also be changed from the settings panel in the UI.

## How it works, briefly

```
link/file -> yt-dlp or upload -> faster-whisper transcript
        -> sentence segmentation -> virality scoring (heuristics + optional LLM)
        -> ffmpeg render (crop, captions, branding) -> clips + social copy
```

The interesting part is `core/virality_engine.py`, which does the moment scoring locally with no model required.

## Project layout

```
server.py            Flask app, localhost-only API
core/
  pipeline.py        end-to-end job orchestration
  virality_engine.py moment detection & scoring
  transcriber.py     faster-whisper wrapper
  renderer.py        ffmpeg clip rendering
  subtitle_engine.py caption generation & styling
  face_tracker.py    face-aware vertical cropping
  llm.py             Gemini + OpenAI-compatible providers
  downloader.py      yt-dlp ingestion
  jobs.py            job queue & persistence
static/ templates/   the UI
```

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Good first areas: more source hosts, better moment scoring, caption styles, render presets. Keep it localhost-only and keep secrets out of the repo.

## License

MIT — do whatever you want with it, just don't be weird about it.
