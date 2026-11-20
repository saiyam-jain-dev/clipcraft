#!/bin/bash
# Production startup script for VPS / Docker deployment
# Usage: ./start-production.sh

set -e

echo "🚀 Starting ClipCraft Backend (Production)"

# Load environment variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    echo "✅ Loaded .env"
else
    echo "⚠️  No .env file found, using defaults"
fi

# Create data directories
mkdir -p data/{downloads,outputs,uploads,jobs}

# Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "❌ FFmpeg not found! Install with: apt-get install ffmpeg"
    exit 1
fi

# Check Python deps
echo "🔍 Checking Python dependencies..."
python -c "import flask; import faster_whisper; import cv2; import yt_dlp; print('✅ All deps OK')"

# Start server
echo "🌐 Starting server on port ${CLIPCRAFT_PORT:-5004}..."
exec python server.py