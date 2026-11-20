"""Optical-flow motion detection for highlight finding.

Ported from NeonClipping's highlights.py: the video is decoded as tiny grayscale
rawvideo by parallel FFmpeg processes (decode is the bottleneck), Farneback optical
flow runs at 3 fps on 160x90 frames, and hard scene cuts are detected and excluded
from the motion score (they are counted separately as cut density).

Signals per second:
  motion - 90th-percentile flow magnitude (fast objects, explosions, camera whips)
  cuts   - hard scene cuts / camera swaps per second
Both are returned as z-scores so they drop straight into virality_engine's
_energy() helper, which expects {"offset", "hop", "z", "audible"}.
"""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np

from .config import FFMPEG_EXE

FPS = 3
W, H = 160, 90
CUT_DIFF = 38.0  # mean abs pixel diff above this = hard cut


def _chunk(video_path: str, start: float, length: float):
    """Decode [start, start+length) to gray frames; return per-second (motion, cuts)."""
    cmd = [FFMPEG_EXE, "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", video_path,
           "-an", "-vf", f"fps={FPS},scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=W * H * 64)
    flow, cuts, prev, n = [], [], None, 0
    try:
        while True:
            raw = proc.stdout.read(W * H)
            while raw and len(raw) < W * H:
                more = proc.stdout.read(W * H - len(raw))
                if not more:
                    break
                raw += more
            if len(raw) != W * H:
                break
            cur = np.frombuffer(raw, np.uint8).reshape(H, W)
            if prev is None:
                flow.append(0.0)
                cuts.append(0.0)
            else:
                if float(np.mean(cv2.absdiff(cur, prev))) > CUT_DIFF:
                    flow.append(0.0)   # a cut is not motion
                    cuts.append(1.0)
                else:
                    f = cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 2, 9, 2, 5, 1.1, 0)
                    flow.append(float(np.percentile(np.hypot(f[..., 0], f[..., 1]), 90)))
                    cuts.append(0.0)
            prev = cur
            n += 1
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    k = len(flow) // FPS * FPS
    f = np.array(flow[:k]).reshape(-1, FPS).mean(1) if k else np.zeros(0)
    c = np.array(cuts[:k]).reshape(-1, FPS).sum(1) if k else np.zeros(0)
    return f, c


def motion_profile(video_path: str, start: float = 0.0, end: float = None,
                   hop: float = 1.0, report=None) -> dict:
    """Per-second motion z-scores over [start, end). Parallel minute-sized chunks."""
    try:
        probe = subprocess.run(
            [FFMPEG_EXE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path], capture_output=True, text=True, timeout=30)
        duration = float(probe.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        duration = 0.0
    if end is None or end <= start:
        end = duration or start + 1
    span = max(0.0, end - start)
    if span <= 0:
        return {"offset": start, "hop": hop, "z": np.array([]), "cuts": np.array([]), "audible": False}

    workers = max(2, min(12, (os.cpu_count() or 4) - 2))
    size = max(60.0, float(np.ceil(span / workers / 60) * 60))  # whole-minute chunks
    starts = [start + s for s in np.arange(0, span, size)]
    done = [0.0]

    def rep(sec):
        done[0] += sec
        if report:
            report(min(done[0], span))

    parts = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(_chunk, video_path, s, min(size, end - s)): s for s in starts}
        for fut in as_completed(jobs):
            parts[jobs[fut]] = fut.result()
            rep(size)
    flow = np.concatenate([parts[s][0] for s in sorted(parts)]) if parts else np.zeros(0)
    cuts = np.concatenate([parts[s][1] for s in sorted(parts)]) if parts else np.zeros(0)

    if not len(flow):
        return {"offset": start, "hop": hop, "z": np.array([]), "cuts": np.array([]), "audible": False}

    def zscore(x):
        mu, sd = (x.mean(), x.std() + 1e-6) if len(x) > 10 else (x.mean(), 1.0)
        return (x - mu) / sd

    return {"offset": start, "hop": hop, "z": zscore(flow).astype(np.float32),
            "cuts": cuts.astype(np.float32), "audible": True}


def detect_scene_cuts(video_path: str, threshold: float = 0.3) -> list:
    """Detects scene cuts/abrupt transitions in video. Returns timestamps of cuts."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    prev_hist, cuts, frame_count = None, [], 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_count % int(fps) != 0:
            frame_count += 1
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()
        hist = hist / hist.sum()
        if prev_hist is not None:
            diff = cv2.compareHist(hist, prev_hist, cv2.HISTCMP_BHATTACHARYYA)
            if diff > threshold:
                cuts.append(frame_count / fps)
        prev_hist = hist
        frame_count += 1
    cap.release()
    return cuts
