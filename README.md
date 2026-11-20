# ClipCraft

A free, local AI clipping studio with a modern Next.js frontend. Paste a YouTube link (or any supported video URL), or drop in a file, and it finds the moments worth posting — then renders vertical 9:16 clips with captions, titles, and ready-to-paste social copy.

## Architecture

ClipCraft now uses a microservices architecture:

- **Frontend**: Next.js 15 with TypeScript and React (http://localhost:3000)
- **Backend**: Python Flask with faster-whisper, FFmpeg (http://127.0.0.1:5004)

Everything stays on `127.0.0.1`. Nothing gets uploaded anywhere except the video download itself and whatever AI API you choose to configure.

## What it does

- **Finds real moments, not chunks.** It transcribes the video with word-level timestamps, groups speech into sentences, and scores candidates on hook strength, whether the moment stands on its own, and how it lands as a short. Audio energy spikes and visual motion help when there's little talking.
- **Renders proper vertical clips.** 1080x1920 MP4s with burned-in captions (bold / clean / minimal styles), optional face-aware cropping for talking heads, and gameplay-friendly layouts.
- **Gives you the package.** Each clip comes with a virality score, a title, a short "why this works" note, a preview, a download link, and copy-paste captions/titles for socials.
- **Lets you tweak before you commit.** Preview frames render fast, you can trim in/out points, then re-render just that clip.
- **Uses a fast model for clip selection.** The recommended setup is Groq's OpenAI-compatible endpoint with `openai/gpt-oss-120b`; add a Groq API key in Settings. Gemini and other OpenAI-compatible endpoints are also supported. No key? Local heuristics still work.
- **Handles long videos.** Whisper transcription and LLM scoring chunk large files automatically.

## Requirements

- Node.js 18+
- Python 3.10+
- FFmpeg
- A few GB of disk for models and renders
- (Optional) an API key for a supported LLM provider

## Quick Start

### Windows

Run the included startup script:
```bash
start.bat
```

This will start both the Python backend and Next.js frontend.

### Manual Setup

1. Install Python dependencies:
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Install frontend dependencies:
```bash
cd nextjs-frontend
npm install
```

3. Configure settings (optional):
```bash
cp settings.example.json settings.json
# Edit settings.json with your API keys
```

4. Start the backend:
```bash
python server.py
```

5. Start the frontend (in a new terminal):
```bash
cd nextjs-frontend
npm run dev
```

6. Open http://localhost:3000

## Project Layout

```
├── server.py              # Flask backend (video processing)
├── core/                  # Backend logic
│   ├── pipeline.py        # End-to-end job orchestration
│   ├── virality_engine.py # Moment detection & scoring
│   ├── transcriber.py     # faster-whisper wrapper
│   ├── renderer.py        # ffmpeg clip rendering
│   ├── subtitle_engine.py # Caption generation & styling
│   ├── face_tracker.py    # Face-aware vertical cropping
│   ├── llm.py             # Gemini + OpenAI-compatible providers
│   ├── downloader.py      # yt-dlp ingestion
│   └── jobs.py            # Job queue & persistence
├── nextjs-frontend/       # Next.js frontend
│   ├── src/
│   │   ├── app/          # Next.js app directory
│   │   ├── components/   # React components
│   │   ├── lib/          # API client & utilities
│   │   └── types/        # TypeScript types
│   └── public/           # Static assets
├── outputs/               # Rendered clips
├── uploads/               # Uploaded videos
└── jobs/                  # Job state
```

## Settings

`settings.json` is gitignored on purpose — it holds your API keys. A template ships as `settings.example.json`. The important knobs:

| Key | What it does |
| --- | --- |
| `llm_provider` | `gemini`, `openai_compatible`, or `none` |
| `gemini_api_key` / `openai_api_key` | Your keys (env vars work too) |
| `openai_base_url` / `openai_model` | Any OpenAI-compatible endpoint |
| `fallback_providers` | JSON list of extra engines, tried in order |
| `whisper_model` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `cookies_from_browser` | `chrome`, `edge`, `firefox`, etc. |
| `brand_name` | Watermark/branding text for clips |

Everything can also be changed from the settings panel in the UI.

## Development

### Backend Development

The Flask backend serves the API for video processing. It remains largely unchanged from the original version.

```bash
python server.py
```

### Frontend Development

The Next.js frontend is a modern React application with TypeScript.

```bash
cd nextjs-frontend
npm run dev
```

### API Configuration

The frontend communicates with the backend via the `NEXT_PUBLIC_API_URL` environment variable. Default is `http://127.0.0.1:5004`.

Edit `nextjs-frontend/.env.local` to change this:

```
NEXT_PUBLIC_API_URL=http://127.0.0.1:5004
```

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Good first areas: more source hosts, better moment scoring, caption styles, render presets. Keep it localhost-only and keep secrets out of the repo.

## License

MIT — do whatever you want with it, just don't be weird about it.
