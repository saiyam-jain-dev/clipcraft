# ClipCraft - Project Documentation

## Overview
ClipCraft is a local-first AI clipping studio that turns long videos into short vertical clips with captions. It runs entirely on your machine using:
- **Backend**: Python Flask on `http://127.0.0.1:5004`
- **Frontend**: Next.js 15 on `http://localhost:3000`
- **AI**: Gemini or OpenAI-compatible APIs (optional; falls back to heuristics)

## Quick Start
```bash
# Start backend
python server.py

# Start frontend (in nextjs-frontend/)
npm run dev

# Or use the bundled script
start.bat
```

Visit `http://localhost:3000` to use the app.

---

## Architecture

### Backend (Flask)
**Entry point**: `server.py`

**Key modules in `core/`**:
- `pipeline.py` - Main orchestration: analyze → find moments → render clips
- `virality_engine.py` - Highlights scoring using heuristics or AI
- `transcriber.py` - Whisper transcription with word timing
- `downloader.py` - yt-dlp for YouTube/video downloads
- `remote_source.py` - YouTube native captions, audio extraction
- `renderer.py` - FFmpeg rendering with face tracking
- `face_tracker.py` - Face/activity analysis (legacy, replaced by motion.py)
- `motion.py` - NEW: Optical flow motion detection (NeonClipping)
- `action.py` - NEW: On-screen action cues (flashes, strobes, cuts)
- `learn.py` - NEW: Feedback loop for learned scoring weights
- `translit.py` - NEW: Devanagari → Hinglish transliteration
- `feedback.py` - Clip rating persistence
- `jobs.py` - Job persistence and updates
- `llm.py` - AI provider management (Gemini, OpenAI-compatible, fallbacks)
- `config.py` - Settings and paths

### Frontend (Next.js 15)
**Entry point**: `nextjs-frontend/src/app/page.tsx`

**Components**:
- `Header.tsx` - Navigation bar
- `HomeView.tsx` - Create clip form (source, options, captions)
- `JobView.tsx` - Job progress, clip cards, ratings, re-editing
- `ProjectsView.tsx` - Job list, search, delete
- `SettingsModal.tsx` - AI provider, Whisper, download settings
- `YoutubeAuthModal.tsx` - YouTube authentication

**Styling**: Custom CSS in `nextjs-frontend/src/app/globals.css` (copied from `public/static/style.css`)

---

## Data Flow

### 1. Create Clip Job
User fills form → `POST /api/clip` → `server.py` creates job → enqueues `run_clipcraft_pipeline`

### 2. Analysis Stage (`pipeline.py`)
```
source → download/extract audio → transcribe (Whisper or YouTube captions)
       → build sentences → audio energy profile → raised voice profile
       → (Deep mode only) motion profile (optical flow)
```

### 3. Moment Finding (`virality_engine.py`)
```
transcript + energy + (optional: motion/raised_voice)
    → find_viral_moments()
        → if AI configured: LLM scores candidates
        → else: heuristic scoring (emotional keywords, energy peaks)
        → returns N best clips with scores
```

### 4. Feature Scoring (NEW)
```
clips → _score_clips()
    → (Deep mode only) download low-res sections → action_cues.analyze()
    → attach 0..1 features (hook, flow, value, trend, energy, motion, flash, strobe, cuts, shout)
    → blend editor score with learned weights (learn.py)
    → re-rank clips
```

### 5. Rendering (`renderer.py`)
```
for each clip:
    → download video (if remote)
    → track faces (using existing trajectory or fresh analysis)
    → generate preview frames
    → render_clip():
        - Apply layout (11 options: auto, fill, fit, split, gameplay, pip_*4, circle, side)
        - Burn in captions (4 styles: bold, clean, minimal, pop)
        - Add branding watermark (if enabled)
        - Export 1080x1920 MP4
```

### 6. Re-editing (NEW)
```
User clicks "Edit" on clip → selects new layout/style/trim
    → POST /api/jobs/{id}/clips/{id}/reedit
    → rerender_clip() with overrides
    → No re-analysis, just fresh render
```

---

## New Features from NeonClipping

### 1. Fast vs Deep Mode
- **Fast** (default): Native captions, audio-only analysis, minimal video work
- **Deep**: Optical flow motion detection, on-screen action analysis, visual scoring

### 2. Optical Flow Motion Detection (`core/motion.py`)
- Parallel FFmpeg-chunk decoding at 160×90, 3fps
- Farneback optical flow → 90th percentile magnitude
- Hard scene cuts excluded from motion, counted separately
- Returns per-second z-scores for virality scoring

### 3. Action Cues (`core/action.py`)
- Measures on-screen action in candidate windows:
  - motion (fast objects, camera movement)
  - flash (explosions, muzzle flashes)
  - strobe (police lights, red/blue alternation)
  - cuts (editing density)
- All values normalized 0..1

### 4. Feedback Loop (`core/learn.py`)
- Clips get 0..1 feature vectors (hook, flow, value, trend, energy, motion, flash, strobe, cuts, shout)
- Star ratings (1-5) saved to `downloads/feedback.jsonl`
- Ridge regression refits scoring weights from ratings
- Weights saved to `downloads/weights.json`
- Blended into clip scores gradually (alpha = n/60, max 0.85)

### 5. Hinglish Support (`core/translit.py`)
- Devanagari → casual Roman letters (Hinglish)
- Word-by-word transliteration (preserves timings)
- Schwa deletion (kamal, not kamala)
- Common-word dictionary (hai, nahi, kya, mein...)
- Applied to Hindi captions or Whisper output

### 6. 11 Layout Options
Original 5: auto, fill, fit, split, gameplay
New 6: pip_tl, pip_tr, pip_bl, pip_br (facecam overlays), circle (circle facecam), side (diptych)

### 7. Pop Caption Style
- Hormozi-style oversized uppercase karaoke
- Spoken word scaled up as it's spoken

### 8. Clip Re-editing
- Saved clips can be re-rendered with new layout/style/trim
- No re-analysis needed
- API: `POST /api/jobs/{id}/clips/{id}/reedit`

---

## API Endpoints

### Jobs
- `POST /api/clip` - Create new clip job
- `GET /api/jobs` - List all jobs
- `GET /api/jobs/{id}` - Get job details
- `DELETE /api/jobs/{id}` - Delete job
- `POST /api/jobs/{id}/retry` - Retry failed job
- `POST /api/jobs/{id}/clips/{id}/trim` - Trim clip times
- `POST /api/jobs/{id}/clips/{id}/reedit` - Re-edit with layout/style overrides
- `POST /api/jobs/{id}/clips/{id}/feedback` - Rate clip (1-5 stars)

### Video
- `POST /api/video-info` - Get video metadata (YouTube, etc.)

### Settings
- `GET /api/settings` - Get current settings
- `POST /api/settings` - Update settings

### YouTube
- `GET /api/youtube/auth-status` - Check auth status
- `POST /api/youtube/authenticate` - Start OAuth flow
- `POST /api/youtube/upload` - Upload clip to YouTube

### Feedback
- `GET /api/feedback/summary` - Get ratings count, fitted weights, insights

---

## Configuration

### Settings (`settings.json` or env vars)
```json
{
  "llm_provider": "gemini" | "openai_compatible" | "none",
  "gemini_api_key": "...",
  "gemini_model": "gemini-3.5-flash-lite",
  "openai_base_url": "https://api.openai.com/v1",
  "openai_model": "gpt-4",
  "openai_api_key": "...",
  "whisper_model": "small" | "base" | "tiny" | "medium" | "large-v3",
  "brand_name": "ImSamxyy",
  "cookies_from_browser": "chrome" | "edge" | "firefox" | "" (for yt-dlp)
}
```

### Job Options
```json
{
  "mode": "fast" | "deep",
  "crop_mode": "auto" | "fill" | "fit" | "split" | "gameplay" | "pip_tl" | "pip_tr" | "pip_bl" | "pip_br" | "circle" | "side",
  "focus": "auto" | "story" | "highlights",
  "target_clips": 4,
  "min_duration": 25,
  "max_duration": 60,
  "range_start": 0,
  "range_end": 0,
  "enable_subtitles": true,
  "enable_branding": false,
  "brand_name": "",
  "caption_style": "bold" | "clean" | "minimal" | "pop",
  "caption_color": "#c8ff64",
  "prompt": "",
  "language": "" | "en" | "hi" | "hinglish"
}
```

---

## File Structure

```
clipcraft/
├── server.py              # Flask entry point
├── start.bat              # Windows startup script
├── requirements.txt      # Python dependencies
├── core/
│   ├── pipeline.py        # Main pipeline orchestration
│   ├── virality_engine.py # Highlights scoring
│   ├── transcriber.py     # Whisper transcription
│   ├── downloader.py      # yt-dlp video download
│   ├── remote_source.py   # YouTube captions, audio extraction
│   ├── renderer.py        # FFmpeg rendering
│   ├── face_tracker.py    # Face/activity analysis (legacy)
│   ├── motion.py          # NEW: Optical flow motion
│   ├── action.py          # NEW: On-screen action cues
│   ├── learn.py           # NEW: Feedback weight learning
│   ├── translit.py        # NEW: Hinglish transliteration
│   ├── feedback.py        # Clip rating persistence
│   ├── jobs.py            # Job persistence
│   ├── llm.py             # AI provider management
│   └── config.py          # Settings & paths
├── nextjs-frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx      # Main app
│   │   │   ├── layout.tsx    # Layout
│   │   │   └── globals.css   # Global styles
│   │   ├── components/
│   │   │   ├── Header.tsx
│   │   │   ├── HomeView.tsx
│   │   │   ├── JobView.tsx
│   │   │   ├── ProjectsView.tsx
│   │   │   ├── SettingsModal.tsx
│   │   │   ├── YoutubeAuthModal.tsx
│   │   │   └── Icon.tsx
│   │   ├── lib/
│   │   │   ├── api.ts
│   │   │   └── utils.ts
│   │   └── types/
│   │       └── index.ts
│   ├── public/
│   │   └── static/
│   │       └── style.css   # Original styles (for reference)
│   └── package.json
├── downloads/             # Downloaded videos, audio, feedback
├── outputs/               # Rendered clips
├── jobs/                  # Job metadata
├── uploads/               # Uploaded files
└── bin/                   # FFmpeg, yt-dlp binaries
```

---

## Current Issues / TODOs

### High Priority
1. **AI Provider Slowness** - Long videos (24min) cause AI calls to take 2+ minutes. Consider:
   - Chunking transcripts for AI calls
   - Timeout + fallback to heuristics
   - Showing user a "skip AI" option

2. **Queue Management** - Many test jobs created. User needs to clean up via Projects page.

3. **Deep Mode Testing** - Optical flow and action analysis need real-world testing.

### Medium Priority
1. **Dead Code Cleanup**:
   - `handleTrimClip` in JobView.tsx (superseded by reedit)
   - `/trim` endpoint in server.py
   - `core/captions_yt.py` (duplicate of remote_source.caption_transcript)
   - `core/face_tracker.py` (replaced by motion.py)

2. **Batch Re-editing** - NeonClipping's `reedit.py` applies one layout/style change to all clips from the same video. Currently only per-clip.

3. **Audio-first for all long videos** - Currently only caption-less videos >40min skip video download. Could extend to all videos >10min.

### Low Priority
1. Update `AGENT_HANDOFF.txt` with new modules
2. Add `flask-cors` to requirements.txt
3. Improve error messages for queue position
4. Add visual progress bar for Deep mode scanning

---

## Known Bugs Fixed This Session

1. **Missing `enqueue` import** - Accidentally removed in pipeline.py edit, causing job crashes. Fixed.
2. **Settings validation** - Too strict, rejected valid settings. Fixed by excluding non-editable fields from validation.
3. **CORS errors** - Added `flask-cors` to allow frontend-backend communication across all routes.
4. **Modal positioning** - Fixed missing `position: fixed` and `z-index` in CSS; unified overlay and modal structure.
5. **Tailwind conflicts** - Removed Tailwind from globals.css, restored original CSS structure.
6. **Next.js 404 on Video/Media/Downloads** - Added reverse rewrites in `next.config.ts` to proxy `/video/*`, `/media/*`, `/download/*`, and `/api/*` to Flask backend on port 5004.
7. **Opus-style Clip Cards Mismatch** - Completely overhauled `JobView.tsx` with embedded 9:16 vertical video player, dynamic virality score badge, hook callouts, 4-dimension virality breakdown (hook, flow, value, trend), star rating feedback, and MP4/SRT downloads.
8. **Premature Job Completion & Auto-Render Flow** - Fixed pipeline logic where job was marked `completed` before videos were rendered. Pipeline now renders all clips in parallel while keeping `status="processing"`, advancing progress 60% -> 100%, and marking `completed` only when all clips are rendered and ready.
9. **Missing SocialModal** - Added `SocialModal.tsx` for 1-click copying of YouTube Shorts title/description and Instagram Reels/TikTok copy, with direct YouTube Shorts upload support.
10. **Full Responsiveness** - Upgraded `globals.css` with responsive breakpoints: 3 columns on desktop (>1080px), 2 columns on tablet (768-1080px), 1 column on mobile (<768px), with responsive topbar, inputs, and touch-friendly controls.

---

## Reference: NeonClipping

The following was ported from `https://github.com/iamajmeena/neonclipping`:
- Optical flow motion detection (`highlights.py` → `motion.py`)
- Action cues (`action.py` → `action.py`)
- Feedback learning (`learn.py` → `learn.py`)
- Hinglish transliteration (`translit.py` → `translit.py`)
- 11 layout catalog (`layouts.py` → renderer.py additions)
- Re-editing concept (`reedit.py` → reedit endpoint)

**What was NOT ported:**
- Local LLM integration (Ollama scout in `smart.py`) — ClipCraft uses cloud APIs only
- Batch processing scripts (ClipCraft is job-based)

---

## Testing Checklist

### Backend
- [x] Python compile check: `python -m compileall -q core server.py`
- [x] Transliteration test: `python -c "from core.translit import to_hinglish; print(to_hinglish('नहीं'))"`
- [x] Learn module test: `python -c "from core import learn; print(learn.load_weights())"`
- [x] Motion profile test on a local video
- [x] Action cues test on a local video
- [x] Feedback API: rate a clip, check `downloads/feedback.jsonl` and `weights.json`

### Frontend
- [x] TypeScript check: `cd nextjs-frontend && npx tsc --noEmit`
- [x] All pages render correctly (Home, Job, Projects)
- [x] Settings modal opens/saves
- [x] Fast/Deep mode toggle works
- [x] 11 layout options appear in dropdown
- [x] Hinglish language option appears
- [x] Star ratings work on clip cards
- [x] Re-edit panel appears and submits
- [x] Social copy modal with 1-click copy works

### End-to-End
- [x] YouTube video with native captions (no Whisper)
- [x] Local file upload
- [x] Fast mode (no motion/action)
- [x] Deep mode (motion + action)
- [x] Hinglish transliteration
- [x] Re-edit a saved clip
- [x] Rate clips and check weight adjustment

---

## Notes for Next Developer

1. **AI Configuration** - User currently has OpenRouter configured. If you need to test without AI, clear the `openai_base_url` and `openai_api_key` in Settings or `settings.json`.

2. **Video Files** - Test with a short local video first (<5min) to avoid long AI waits.

3. **Backend Terminal** - The Flask server runs in background. Check logs with:
   ```bash
   curl -s http://127.0.0.1:5004/api/jobs/{id} | python -m json.tool
   ```

4. **Frontend Dev Server** - Needs to be restarted after CSS changes (Ctrl+C, `npm run dev`).

5. **Face Tracking** - Face trajectory is computed once per video and reused. For new layouts that need different framing, the same trajectory is applied via FFmpeg `sendcmd`.

6. **FFmpeg** - Path stored in `core/config.py`. The app creates a local alias in `bin/ffmpeg.exe` for yt-dlp compatibility.

7. **Secrets** - Never log or return API keys to the frontend. `core/config.py.public_settings()` masks them.

---

## Contact
- Original repo: https://github.com/saiyam-jain-dev/clipcraft
- Reference for new features: https://github.com/iamajmeena/neonclipping
