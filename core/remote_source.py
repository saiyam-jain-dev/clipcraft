"""Link sources without downloading the whole video: YouTube captions give the transcript, audio-only
download is the fallback when there are none, and only the chosen clips are downloaded for rendering."""
import os
import re
import glob
import shutil
import subprocess
import cv2
import numpy as np
import requests
import yt_dlp
from yt_dlp.utils import download_range_func
from .config import DOWNLOADS_DIR, FFMPEG_EXE
from .downloader import _ydl_opts, _friendly_error, _extract_with_fallback

# H.264 first (OpenCV face tracking can't reliably decode AV1 on Windows); 30 fps is plenty for Shorts.
VIDEO_FORMAT = ("bv*[height<=1080][fps<=30][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080][fps<=30]+ba/"
                "bv*[height<=1080]+ba/b[height<=1080]/best/b")


def fetch_info(source: str, **extra) -> dict:
    extra.pop("format", None)  # Never restrict metadata extraction so all stream formats are resolved
    process = extra.pop("process", None)
    opts = _ydl_opts(skip_download=True, **extra)
    if process is not None:
        opts["extract_flat"] = not process
    try:
        info = _extract_with_fallback(opts, source, download=False)
    except Exception as e:
        raise RuntimeError(_friendly_error(e)) from e

    if info.get("is_live"):
        raise RuntimeError("Live streams cannot be clipped until the recording has finished.")
    if info.get("_type") == "playlist":
        raise RuntimeError("Paste a single video link, not a playlist.")
    return info


def check_space(needed_bytes: float):
    free = shutil.disk_usage(DOWNLOADS_DIR).free
    if free < needed_bytes + 2 * 1024 ** 3:
        raise RuntimeError(f"Not enough disk space: this needs about {needed_bytes / 1024 ** 3:.1f} GB "
                           f"and only {free / 1024 ** 3:.1f} GB is free (2 GB is kept spare).")


def _progress_hook(progress_callback, label):
    def hook(d):
        if d.get("status") != "downloading" or not progress_callback:
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        pct = d.get("downloaded_bytes", 0) / total * 100 if total else 0
        size = f" of {total / 1048576:.0f} MB" if total else ""
        speed = f" at {d['speed'] / 1048576:.1f} MB/s" if d.get("speed") else ""
        eta = f", {int(d['eta']) // 60}:{int(d['eta']) % 60:02d} left" if d.get("eta") is not None else ""
        progress_callback(pct, f"{label} {pct:.0f}%{size}{speed}{eta}")
    return hook


# ----------------------------------------------------------------------------- captions
def _caption_track(info: dict):
    """(url, language, kind). Auto '-orig' captions have per-word timing; uploaded ones are line-timed."""
    lang = (info.get("language") or "").split("-")[0]
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    def json3(tracks):
        return next((t["url"] for t in tracks or [] if t.get("ext") == "json3"), None)

    candidates = []
    if lang:
        candidates += [(auto, f"{lang}-orig", "auto"), (manual, lang, "uploaded")]
    candidates += [(auto, k, "auto") for k in auto if k.endswith("-orig")]
    candidates += [(manual, k, "uploaded") for k in manual if k != "live_chat"]
    for tracks, key, kind in candidates:
        url = json3(tracks.get(key))
        if url:
            return url, key.replace("-orig", ""), kind
    return None


def _json3_words(data: dict) -> list:
    words = []
    for ev in data.get("events", []):
        segs = [s for s in ev.get("segs") or [] if s.get("utf8", "").strip()]
        if not segs:
            continue
        t0 = ev.get("tStartMs", 0) / 1000
        t1 = (ev.get("tStartMs", 0) + ev.get("dDurationMs", 0)) / 1000
        if len(segs) > 1 and all("tOffsetMs" in s for s in segs[1:]):
            spans = [(t0 + s.get("tOffsetMs", 0) / 1000, s["utf8"]) for s in segs]
        else:
            spans = [(t0, " ".join(s["utf8"] for s in segs))]
        for k, (start, text) in enumerate(spans):
            end = spans[k + 1][0] if k + 1 < len(spans) else t1
            tokens = [t for t in text.split() if not re.fullmatch(r"\[[^\]]*\]", t)]
            step = max(0.05, (end - start) / max(1, len(tokens)))
            for n, tok in enumerate(tokens):
                words.append({"word": tok, "start": round(start + n * step, 2),
                              "end": round(start + (n + 1) * step, 2)})
    words.sort(key=lambda w: w["start"])
    for a, b in zip(words, words[1:]):
        a["end"] = round(min(a["end"], b["start"], a["start"] + 1.5), 2)
    if words:
        words[-1]["end"] = round(min(words[-1]["end"], words[-1]["start"] + 1.5), 2)
    return [w for w in words if w["end"] > w["start"]]


def caption_transcript(info: dict, start: float, end: float):
    track = _caption_track(info)
    if not track:
        return None
    url, lang, kind = track
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    words = [w for w in _json3_words(r.json()) if w["start"] >= start and (not end or w["end"] <= end)]
    if len(words) < 20:
        return None
    return {"language": lang, "words": words, "kind": kind}


# ----------------------------------------------------------------------------- audio fallback
def download_audio(info: dict, source: str, start: float, end: float, progress_callback=None) -> str:
    vid = re.sub(r"[^A-Za-z0-9_-]", "", info.get("id") or "video")
    duration = info.get("duration") or 0
    ranged = start > 0 or bool(end and end < duration)
    base = os.path.join(DOWNLOADS_DIR, f"aud_{vid}" + (f"_{int(start)}-{int(end)}" if ranged else ""))
    cached = [p for p in glob.glob(base + ".*") if not p.endswith((".part", ".wav", ".ytdl"))]
    if cached:
        return cached[0]
    audio = [f for f in info.get("formats") or []
             if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best = max(audio, key=lambda f: f.get("abr") or 0, default={})
    size = best.get("filesize") or best.get("filesize_approx") or (best.get("abr") or 160) * 125 * duration
    check_space(size * ((end - start) / duration if duration else 1))
    opts = _ydl_opts(format="ba[ext=m4a]/ba/b/best", outtmpl=base + ".%(ext)s",
                     progress_hooks=[_progress_hook(progress_callback, "Downloading audio only...")])
    if ranged:
        opts["download_ranges"] = download_range_func(None, [(start, end)])
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(source, download=True)
    except Exception as e:
        # Fallback to broader format selector if specific audio format was unavailable
        try:
            fallback_opts = dict(opts)
            fallback_opts["format"] = "bestaudio/best"
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                res = ydl.extract_info(source, download=True)
        except Exception:
            raise RuntimeError(_friendly_error(e)) from e
    return res["requested_downloads"][0]["filepath"]


def loudness_profile(audio_path: str, offset: float = 0.0, hop: float = 0.5, sr: int = 8000) -> dict:
    """Same shape as virality_engine.audio_energy_profile, streamed so hours of audio never become a WAV."""
    proc = subprocess.Popen([FFMPEG_EXE, "-v", "error", "-i", audio_path, "-vn", "-ac", "1", "-ar", str(sr),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    hop_n, db, rest = int(sr * hop), [], np.zeros(0, dtype=np.float32)
    while True:
        raw = proc.stdout.read(hop_n * 2 * 600)
        if not raw:
            break
        x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        x = np.concatenate([rest, x])
        n = len(x) // hop_n
        if n:
            frames = x[: n * hop_n].reshape(n, hop_n)
            db.extend(20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6))
        rest = x[n * hop_n:]
    proc.wait()
    db = np.array(db, dtype=np.float32)
    voiced = db[db > -50]
    mu, sd = (voiced.mean(), voiced.std() + 1e-6) if len(voiced) > 10 else (db.mean(), db.std() + 1e-6)
    return {"offset": offset, "hop": hop, "z": (db - mu) / sd, "audible": bool(len(voiced))}


def loud_windows(profile: dict, count: int, length: float) -> list:
    """The `count` non-overlapping windows of `length` seconds with the highest mean loudness."""
    z, hop, offset = profile["z"], profile["hop"], profile["offset"]
    n = max(1, int(length / hop))
    if len(z) <= n:
        return [(offset, offset + len(z) * hop)]
    score = np.convolve(z, np.ones(n) / n, mode="valid")
    picks = []
    for i in np.argsort(score)[::-1]:
        if all(abs(int(i) - j) >= n for j in picks):
            picks.append(int(i))
            if len(picks) >= count:
                break
    return sorted((round(offset + i * hop, 2), round(offset + (i + n) * hop, 2)) for i in picks)


def _resolve_stream_formats(info: dict) -> list:
    """Safely extracts valid stream URLs whether info came from process=True or process=False.
    Prefers synchronized combined H.264/HLS streams (like format 301/300/18) that seek reliably."""
    formats = info.get("formats") or []
    # 1. Combined audio+video format (e.g. 1080p m3u8 format 301 or progressive 720p format 18/22)
    combined = [f for f in formats if f.get("url") and f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
    if combined:
        under_1080 = [f for f in combined if (f.get("height") or 0) <= 1080]
        pool = under_1080 or combined
        return [max(pool, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))]

    fmts = info.get("requested_formats")
    if fmts and all(isinstance(f, dict) and f.get("url") for f in fmts):
        return fmts

    # 2. Separate video and audio streams
    vfmts = [f for f in formats if f.get("url") and f.get("vcodec") not in (None, "none")]
    afmts = [f for f in formats if f.get("url") and f.get("acodec") not in (None, "none")]
    if vfmts and afmts:
        avc_v = [f for f in vfmts if str(f.get("vcodec", "")).startswith("avc1") and (f.get("height") or 0) <= 1080]
        v_pool = avc_v or [f for f in vfmts if (f.get("height") or 0) <= 1080] or vfmts
        best_v = max(v_pool, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        best_a = max(afmts, key=lambda f: (f.get("abr") or 0, f.get("tbr") or 0))
        return [best_v, best_a]
    if vfmts:
        return [max(vfmts, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))]
    playable = [f for f in formats if f.get("url") and f.get("protocol") != "mhtml" and (f.get("vcodec") not in (None, "none") or f.get("acodec") not in (None, "none"))]
    if playable:
        return [playable[-1]]
    if "url" in info:
        return [info]
    raise RuntimeError("Could not find playable video streams for this video (YouTube returned bot verification or format restriction).")


def _get_cookie_header() -> str:
    """Extracts a 'Cookie: name=value; ...' string from the configured yt-dlp cookies file."""
    cookie_file = None
    try:
        from .downloader import _ydl_opts
        opts = _ydl_opts()
        if opts.get("cookiefile") and os.path.isfile(opts["cookiefile"]):
            cookie_file = opts["cookiefile"]
    except Exception:
        pass
    if not cookie_file:
        for candidate in [
            "/tmp/youtube_cookies.txt",
            "/app/data/youtube_cookies.txt",
            os.path.join(os.path.dirname(DOWNLOADS_DIR), "youtube_cookies.txt"),
            os.path.join(DOWNLOADS_DIR, "youtube_cookies.txt"),
        ]:
            if os.path.isfile(candidate) and os.path.getsize(candidate) >= 100:
                cookie_file = candidate
                break
    if not cookie_file:
        return ""
    try:
        cookies = []
        with open(cookie_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies.append(f"{parts[5]}={parts[6]}")
        return "; ".join(cookies)
    except Exception:
        return ""


def _has_valid_video(path: str) -> bool:
    """Verifies that an extracted video file contains playable video frames."""
    if not os.path.isfile(path) or os.path.getsize(path) < 20000:
        return False
    try:
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None and frame.shape[0] > 0 and frame.shape[1] > 0:
                return True
    except Exception:
        pass
    return os.path.isfile(path) and os.path.getsize(path) > 50000


# ----------------------------------------------------------------------------- per-clip video
def download_section(source: str, start: float, end: float, out_path: str, info: dict = None, gpu_id: int = None) -> str:
    """Frame-accurate cut of [start, end] straight from the stream URLs (HTTP range seeks, no full download)."""
    fmts = []
    if info:
        try:
            fmts = _resolve_stream_formats(info)
        except Exception:
            fmts = []
    if not fmts:
        try:
            info = fetch_info(source, format=VIDEO_FORMAT)
            fmts = _resolve_stream_formats(info)
        except Exception:
            info = fetch_info(source, format="best/b")
            fmts = _resolve_stream_formats(info)
    is_hls = any(f.get("protocol") in ("m3u8", "m3u8_native") or ".m3u8" in f.get("url", "") for f in fmts)
    cookie_hdr = _get_cookie_header()
    print(f"[DOWNLOAD_SECTION START] is_hls={is_hls} fmts_len={len(fmts)} formats={[(f.get('format_id'), f.get('protocol'), f.get('vcodec'), f.get('acodec'), f.get('url')[:60] if f.get('url') else None) for f in fmts]}", flush=True)

    cmd = [FFMPEG_EXE, "-y", "-v", "warning"]
    for f in fmts:
        headers = dict(f.get("http_headers") or info.get("http_headers") or {})
        # Only inject Cookie header for non-HLS streams (HLS URLs already carry auth tokens in query; 6KB cookies cause 400 Bad Request on manifest servers)
        if not is_hls and cookie_hdr and "Cookie" not in headers:
            headers["Cookie"] = cookie_hdr
        if headers:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        cmd += ["-ss", f"{start:.3f}", "-i", f["url"]]
    cmd += ["-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", f"{len(fmts) - 1}:a:0?", "-avoid_negative_ts", "make_zero"]

    # Only attempt stream copy on direct non-HLS streams; HLS requires re-encoding to avoid broken timestamps / 0 frames
    if not is_hls:
        try:
            r_copy = subprocess.run(cmd + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out_path],
                                    capture_output=True, text=True, timeout=30)
            if r_copy.returncode == 0 and _has_valid_video(out_path):
                return out_path
        except Exception:
            pass
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass

    # Fallback to NVENC (if GPU available) or ultrafast libx264 CPU transcode
    cmd_transcode = cmd + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    last_err = ""
    has_gpu = bool(shutil.which("nvidia-smi"))
    candidates = []
    if has_gpu:
        nv_args = ["-c:v", "h264_nvenc"]
        if gpu_id is not None:
            nv_args += ["-gpu", str(gpu_id)]
        nv_args += ["-preset", "p4", "-rc", "vbr", "-cq", "20", "-b:v", "0"]
        candidates.append(nv_args)
    cpu_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-threads", "4"]
    candidates.append(cpu_args)

    for video_args in candidates:
        r = subprocess.run(cmd_transcode + video_args + [out_path], capture_output=True, text=True, timeout=900)
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        print(f"[TRANSCODE RESULT] encoder={video_args[1]} code={r.returncode} size={size}", flush=True)
        if r.returncode == 0 and size > 20000:
            return out_path
        last_err = (r.stderr or "").strip() or "Transcoding failed to produce an output file"
        print(f"[TRANSCODE FAILED] stderr={last_err[-800:]}", flush=True)
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass

    raise RuntimeError(f"Could not download this part of the video: {last_err.strip()[-300:]}")


def download_lowres_section(source: str, start: float, end: float, out_path: str,
                            info: dict = None) -> str:
    """A tiny <=240p copy of just [start, end] — only good enough to *look* at the
    action (motion, flashes, strobes), never for rendering."""
    if info is None:
        info = fetch_info(source)
    opts = _ydl_opts(format="worst[ext=mp4][height<=240]/worst[height<=240]/worst",
                     outtmpl=out_path + ".%(ext)s",
                     download_ranges=download_range_func(None, [(max(0.0, start), end)]))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(source, download=True)
    except Exception as e:
        raise RuntimeError(_friendly_error(e)) from e
    return res["requested_downloads"][0]["filepath"]


def preview_frame(info: dict, t: float, out_path: str, source: str = None) -> str:
    """One frame at time `t` pulled straight from the stream (HTTP range seek) — no clip download."""
    try:
        fmts = _resolve_stream_formats(info)
    except Exception:
        url = source or info.get("webpage_url") or info.get("original_url")
        if url:
            info = fetch_info(url)
            fmts = _resolve_stream_formats(info)
        else:
            raise
    vfmt = next((f for f in fmts if f.get("vcodec") not in (None, "none")), fmts[0])
    is_hls = any(f.get("protocol") in ("m3u8", "m3u8_native") or ".m3u8" in f.get("url", "") for f in fmts)
    cookie_hdr = _get_cookie_header()
    cmd = [FFMPEG_EXE, "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}"]
    headers = dict(vfmt.get("http_headers") or info.get("http_headers") or {})
    if not is_hls and cookie_hdr and "Cookie" not in headers:
        headers["Cookie"] = cookie_hdr
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd += ["-i", vfmt["url"], "-frames:v", "1",
            "-vf", "scale=360:640:force_original_aspect_ratio=increase,crop=360:640",
            "-q:v", "3", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"Could not grab a preview frame: {r.stderr.strip()[-200:]}")
    return out_path
