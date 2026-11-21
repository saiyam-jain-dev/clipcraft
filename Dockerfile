# ClipCraft Backend - Multi-arch (amd64/arm64)
# Build: docker buildx build --platform linux/amd64,linux/arm64 -t clipcraft-backend .
# Run:   docker run -d -p 5004:5004 -v ./data:/app/data -v ./settings.json:/app/settings.json clipcraft-backend

FROM python:3.11-slim AS base

# Install Deno binary (official multi-arch) for yt-dlp native JavaScript challenge solver
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# System deps: FFmpeg, OpenCV, yt-dlp, whisper.cpp deps, Node.js (for yt-dlp JS deciphering), fonts for ASS subtitles
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    ca-certificates \
    curl \
    fontconfig \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY server.py .
COPY core/ ./core/
COPY bin/ ./bin/

# Create data dirs
RUN mkdir -p /app/data/{downloads,outputs,uploads,jobs}
COPY youtube_cookies.txt* /app/data/

# Settings file (mounted at runtime)
ENV SETTINGS_FILE=/app/settings.json

# Non-root user
RUN useradd -m -u 1000 clipcraft && chown -R clipcraft:clipcraft /app
USER clipcraft

EXPOSE 5004

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5004/api/health || exit 1

CMD ["python", "server.py"]