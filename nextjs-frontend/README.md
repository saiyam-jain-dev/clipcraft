# ClipCraft Next.js Frontend

This is the Next.js frontend for ClipCraft - a local AI clipping studio. It communicates with the Python Flask backend for video processing.

## Architecture

This is a microservices architecture:

- **Frontend (Next.js)**: Serves the React UI at http://localhost:3000
- **Backend (Flask/Python)**: Handles video processing, transcription, and rendering at http://127.0.0.1:5004

## Setup

### Prerequisites

- Node.js 18+
- Python 3.10+ (for the backend)
- FFmpeg (for video processing)

### Installation

1. Install frontend dependencies:
```bash
cd nextjs-frontend
npm install
```

2. Configure the backend API URL:
```bash
# Edit .env.local or set environment variable
NEXT_PUBLIC_API_URL=http://127.0.0.1:5004
```

3. Start the Python backend (from the parent directory):
```bash
cd ..
python server.py
```

4. Start the Next.js development server:
```bash
cd nextjs-frontend
npm run dev
```

5. Open http://localhost:3000 in your browser

## Project Structure

```
nextjs-frontend/
├── src/
│   ├── app/              # Next.js app directory
│   │   ├── globals.css   # Global styles
│   │   ├── layout.tsx    # Root layout
│   │   └── page.tsx      # Main page component
│   ├── components/       # React components
│   │   ├── Header.tsx    # Navigation header
│   │   ├── HomeView.tsx  # Home/studio view
│   │   ├── JobView.tsx   # Job/clips view
│   │   ├── ProjectsView.tsx # Projects grid
│   │   └── Icon.tsx      # Icon component
│   ├── lib/              # Utilities
│   │   ├── api.ts        # API client
│   │   └── utils.ts      # Helper functions
│   └── types/            # TypeScript types
│       └── index.ts      # Type definitions
├── public/               # Static assets
│   └── static/           # Original static files
└── package.json
```

## Development

### Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm start` - Start production server
- `npm run lint` - Run ESLint

### API Communication

The frontend communicates with the backend via the API client in `src/lib/api.ts`. All API calls are routed through the `NEXT_PUBLIC_API_URL` environment variable.

## Production Deployment

1. Build the Next.js app:
```bash
npm run build
```

2. Start the production server:
```bash
npm start
```

3. Ensure the Flask backend is running and accessible at the configured API URL.

## Backend API

The frontend expects the following API endpoints from the Flask backend:

- `GET /api/health` - Health check
- `GET /api/video-info?url=` - Get video information
- `POST /api/clip` - Create a new clipping job
- `GET /api/jobs` - List all jobs
- `GET /api/jobs/:id` - Get job details
- `DELETE /api/jobs/:id` - Delete a job
- `POST /api/retry/:id` - Retry a failed job
- `POST /api/jobs/:id/clips/:clipId/render` - Render a clip
- `POST /api/jobs/:id/render-all` - Render all clips
- `POST /api/jobs/:id/clips/:clipId/trim` - Trim a clip
- `GET /api/social-package/:jobId/:clipId` - Get social package
- `GET /api/youtube/auth-status` - YouTube auth status
- `POST /api/youtube/authenticate` - Authenticate with YouTube
- `POST /api/youtube/upload/:jobId/:clipId` - Upload to YouTube
- `POST /api/youtube/upload-all/:jobId` - Upload all clips to YouTube
- `GET /api/settings` - Get settings
- `POST /api/settings` - Update settings

## License

MIT
