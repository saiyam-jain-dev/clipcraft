import os
import re
import glob
import shutil
import subprocess
import tempfile
import json
import http.cookiejar
import cv2
import yt_dlp
from .config import DOWNLOADS_DIR, FFMPEG_EXE, NODE_EXE, DENO_EXE, load_settings

ANON_COOKIES_PATH = "/tmp/youtube_cookies_anon.txt" if os.path.exists("/tmp") else os.path.join(tempfile.gettempdir(), "youtube_cookies_anon.txt")


def ensure_netscape_cookies(path: str) -> bool:
    """Detects if cookie file is in JSON or header-less format, and safely converts it to valid Netscape format in-place.
    Also extracts visitor/anon cookies to ANON_COOKIES_PATH to bypass account-level SABR streaming experiments."""
    if not path or not os.path.isfile(path) or os.path.getsize(path) < 10:
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
    except Exception:
        return False

    if not content:
        return False

    anon_names = {"VISITOR_INFO1_LIVE", "VISITOR_PRIVACY_METADATA", "YSC", "PREF", "GPS"}
    anon_lines = [
        "# Netscape HTTP Cookie File",
        "# http://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated file!  Do not edit.",
        "",
    ]
    has_anon = False

    # Case 1: JSON format (e.g. {"cookies": [...]} or [...])
    if content.startswith("{") or content.startswith("["):
        try:
            data = json.loads(content)
            cookie_list = []
            if isinstance(data, dict):
                cookie_list = data.get("cookies") or data.get("data") or []
            elif isinstance(data, list):
                cookie_list = data

            lines = [
                "# Netscape HTTP Cookie File",
                "# http://curl.haxx.se/rfc/cookie_spec.html",
                "# This is a generated file!  Do not edit.",
                "",
            ]
            for c in cookie_list:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                value = c.get("value")
                if not name or value is None:
                    continue
                domain = str(c.get("domain", ".youtube.com"))
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                path_val = str(c.get("path", "/"))
                secure = "TRUE" if c.get("secure") else "FALSE"
                raw_exp = c.get("expires") or c.get("expirationDate") or 0
                try:
                    expires = str(int(float(raw_exp)))
                except Exception:
                    expires = "0"
                row = f"{domain}\t{flag}\t{path_val}\t{secure}\t{expires}\t{name}\t{value}"
                lines.append(row)
                if name in anon_names:
                    anon_lines.append(row)
                    has_anon = True

            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            if has_anon:
                try:
                    with open(ANON_COOKIES_PATH, "w", encoding="utf-8") as f_anon:
                        f_anon.write("\n".join(anon_lines) + "\n")
                except Exception:
                    pass
            return True
        except Exception:
            pass

    # Case 2: Plain Netscape format or tab-delimited
    is_netscape = content.startswith("# Netscape HTTP Cookie File") or content.startswith("# HTTP Cookie File")
    if not is_netscape:
        # Check if tab-separated rows exist
        lines = content.splitlines()
        for line in lines:
            if line.strip() and "\t" in line:
                new_content = "# Netscape HTTP Cookie File\n# http://curl.haxx.se/rfc/cookie_spec.html\n\n" + content
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content + "\n")
                    content = new_content
                    is_netscape = True
                except Exception:
                    pass
                break

    # Extract anon cookies from Netscape lines
    if is_netscape:
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            parts = line_str.split("\t")
            if len(parts) >= 7 and parts[5] in anon_names:
                anon_lines.append(line_str)
                has_anon = True
        if has_anon:
            try:
                with open(ANON_COOKIES_PATH, "w", encoding="utf-8") as f_anon:
                    f_anon.write("\n".join(anon_lines) + "\n")
            except Exception:
                pass
        return True

    return False


def format_duration(seconds: float) -> str:
    s = int(seconds or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def ffmpeg_dir() -> str:
    """yt-dlp only recognises a binary named ffmpeg(.exe); imageio-ffmpeg's has a versioned name."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return os.path.dirname(os.path.abspath(system_ffmpeg))
    folder = os.path.join(os.path.dirname(DOWNLOADS_DIR), "bin")
    exe = os.path.join(folder, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not os.path.exists(exe):
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(FFMPEG_EXE, exe)
    return folder


def _ydl_opts(**extra) -> dict:
    settings = load_settings()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignore_no_formats_error": True,
        "ffmpeg_location": ffmpeg_dir(),
    }
    if DENO_EXE and os.path.exists(DENO_EXE):
        opts["js_runtimes"] = {"deno": {"path": DENO_EXE}}
    elif NODE_EXE and os.path.exists(NODE_EXE):
        opts["js_runtimes"] = {"node": {"path": NODE_EXE}}
    else:
        opts["js_runtimes"] = {"deno": {}, "node": {}}

    # Cookies support: Netscape cookies file (servers/Docker) or browser cookies (desktop)
    b64_cookies = os.getenv("YTDLP_COOKIES_B64")
    cookie_file = settings.get("ytdlp_cookies_file") or os.getenv("YTDLP_COOKIES_FILE")

    if b64_cookies:
        # Prefer writing to /tmp/youtube_cookies.txt which is universally writable on all Linux/Docker/Windows
        for target in [
            "/tmp/youtube_cookies.txt",
            cookie_file,
            "/app/data/youtube_cookies.txt",
            os.path.join(DOWNLOADS_DIR, "youtube_cookies.txt"),
        ]:
            if not target:
                continue
            try:
                import base64
                os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(base64.b64decode(b64_cookies))
                if ensure_netscape_cookies(target):
                    cookie_file = target
                    break
            except Exception:
                continue

    if not cookie_file or not os.path.isfile(cookie_file):
        for candidate in [
            "/app/data/youtube_cookies.txt",
            "/tmp/youtube_cookies.txt",
            os.path.join(os.path.dirname(DOWNLOADS_DIR), "youtube_cookies.txt"),
            os.path.join(DOWNLOADS_DIR, "youtube_cookies.txt"),
        ]:
            if os.path.isfile(candidate) and os.path.getsize(candidate) >= 10:
                if ensure_netscape_cookies(candidate):
                    cookie_file = candidate
                    break

    if cookie_file and os.path.isfile(cookie_file) and os.path.getsize(cookie_file) >= 10:
        if ensure_netscape_cookies(cookie_file):
            opts["cookiefile"] = cookie_file
    elif settings.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (settings["cookies_from_browser"],)

    if "extractor_args" in extra:
        ea = extra.pop("extractor_args")
        if isinstance(ea, dict):
            for k, v in ea.items():
                if k in opts["extractor_args"] and isinstance(opts["extractor_args"][k], dict) and isinstance(v, dict):
                    opts["extractor_args"][k].update(v)
                else:
                    opts["extractor_args"][k] = v

    opts.update(extra)
    return opts


def _friendly_error(e: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
    if "Sign in to confirm" in msg or "not a bot" in msg:
        return ("YouTube is asking to confirm you're not a bot. Open Settings and set "
                "'Browser cookies' to the browser you're logged into YouTube with (e.g. chrome, edge).")
    if "Private video" in msg:
        return "This video is private."
    if "Unsupported URL" in msg:
        return "That link isn't a supported video URL."
    return msg[:300]


def _extract_with_fallback(opts: dict, url: str, download: bool = False):
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=download)
            fmts = (info.get("formats") or []) if info else []
            # If no playable video formats were returned (e.g. SABR experiment or suppressed error), trigger fallback
            if not any(f.get("vcodec") not in (None, "none") and f.get("url") for f in fmts):
                raise RuntimeError("No playable video formats returned for current options")
            return info
    except Exception as e:
        msg = str(e)
        # 1. Retry with anonymous visitor cookies (bypasses account-level SABR restriction while preserving anti-bot token)
        if os.path.exists(ANON_COOKIES_PATH) and opts.get("cookiefile") != ANON_COOKIES_PATH:
            try:
                anon_opts = dict(opts)
                anon_opts["cookiefile"] = ANON_COOKIES_PATH
                anon_opts.pop("format", None)
                with yt_dlp.YoutubeDL(anon_opts) as ydl:
                    info = ydl.extract_info(url, download=download)
                    fmts = (info.get("formats") or []) if info else []
                    if any(f.get("vcodec") not in (None, "none") and f.get("url") for f in fmts):
                        return info
            except Exception:
                pass

        # 2. Retry without cookies
        if opts.get("cookiefile") or opts.get("cookiesfrombrowser"):
            try:
                fallback_opts = dict(opts)
                fallback_opts.pop("cookiefile", None)
                fallback_opts.pop("cookiesfrombrowser", None)
                fallback_opts.pop("format", None)
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(url, download=download)
                    fmts = (info.get("formats") or []) if info else []
                    if any(f.get("vcodec") not in (None, "none") and f.get("url") for f in fmts):
                        return info
            except Exception:
                pass
        raise


def get_video_info(url: str) -> dict:
    try:
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(skip_download=True)) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
        except Exception:
            info = _extract_with_fallback(_ydl_opts(skip_download=True), url, download=False)
    except Exception as e:
        raise RuntimeError(_friendly_error(e)) from e
    if info.get("_type") == "playlist":
        raise RuntimeError("That's a playlist link. Paste a single video link.")
    dur = info.get("duration") or 0
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Untitled video",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration_sec": dur,
        "duration_str": format_duration(dur),
        "thumbnail": info.get("thumbnail", ""),
        "extractor": info.get("extractor_key", ""),
        "is_live": bool(info.get("is_live")),
        "webpage_url": info.get("webpage_url", url),
    }


def probe_video(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if not w or not h:
        raise RuntimeError("Could not read the video file (unsupported codec or corrupted file).")
    return {"fps": fps, "duration": frames / fps if fps else 0, "width": w, "height": h}


def download_video(source: str, progress_callback=None, audio_only=False) -> dict:
    """Downloads a URL (or accepts a local file). Returns path + metadata. Downloads are cached by video id.
    
    Args:
        source: Video URL or local file path
        progress_callback: Optional callback(progress_pct, message)
        audio_only: If True, download only audio (much faster for long videos)
    """
    if os.path.isfile(source):
        title = os.path.splitext(os.path.basename(source))[0]
        title = re.sub(r"^[0-9a-f]{8}_", "", title)
        meta = probe_video(source)
        return {"video_path": source, "title": title, "duration": meta["duration"], "thumbnail": "", "id": "", **meta}

    def hook(d):
        if d.get("status") == "downloading" and progress_callback:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = (d.get("downloaded_bytes", 0) / total * 100) if total else 0
            progress_callback(pct, f"Downloading {'audio' if audio_only else 'video'}... {pct:.0f}%")

    try:
        info = _extract_with_fallback(_ydl_opts(skip_download=True), source, download=False)
        if info.get("is_live"):
            raise RuntimeError("Live streams cannot be clipped until the recording has finished.")
        if info.get("_type") == "playlist":
            raise RuntimeError("Paste a single video link, not a playlist.")
        vid = re.sub(r"[^A-Za-z0-9_-]", "", info.get("id") or "video")
        extractor = re.sub(r"[^a-z0-9_-]", "", (info.get("extractor_key") or info.get("extractor") or "generic").lower())
        cache_name = f"src_{extractor}_{vid}"
        
        if audio_only:
            # Audio-only mode: download only the audio track
            audio_cached = [p for p in glob.glob(os.path.join(DOWNLOADS_DIR, f"{cache_name}.m4a"))]
            if audio_cached:
                path = audio_cached[0]
                if progress_callback:
                    progress_callback(100, "Using cached audio...")
            else:
                opts = _ydl_opts(
                    format="bestaudio/best",
                    outtmpl=os.path.join(DOWNLOADS_DIR, f"{cache_name}.%(ext)s"),
                    progress_hooks=[hook],
                )
                info = _extract_with_fallback(opts, source, download=True)
                path = info["requested_downloads"][0]["filepath"]
        else:
            # Full video mode
            cached = [p for p in glob.glob(os.path.join(DOWNLOADS_DIR, f"{cache_name}.*")) if p.endswith(".mp4")]
            if cached:
                path = cached[0]
                if progress_callback:
                    progress_callback(100, "Using cached download...")
            else:
                opts = _ydl_opts(
                    # H.264 first: OpenCV (used for face tracking) can't reliably decode AV1 on Windows.
                    format="bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080][vcodec^=avc1]+ba/"
                           "bv*[height<=1080]+ba/b[height<=1080]/b",
                    merge_output_format="mp4",
                    outtmpl=os.path.join(DOWNLOADS_DIR, f"{cache_name}.%(ext)s"),
                    progress_hooks=[hook],
                )
                info = _extract_with_fallback(opts, source, download=True)
                path = info["requested_downloads"][0]["filepath"]
    except Exception as e:
        raise RuntimeError(_friendly_error(e)) from e

    if audio_only:
        # For audio-only, return metadata without video probe
        return {
            "video_path": path,
            "title": info.get("title") or "Untitled video",
            "thumbnail": info.get("thumbnail", ""),
            "id": info.get("id", ""),
            "webpage_url": info.get("webpage_url", source),
            "duration": info.get("duration") or 0,
            "fps": 0,
            "width": 0,
            "height": 0,
            "audio_only": True,
        }
    
    meta = probe_video(path)
    return {
        "video_path": path,
        "title": info.get("title") or "Untitled video",
        "thumbnail": info.get("thumbnail", ""),
        "id": info.get("id", ""),
        "webpage_url": info.get("webpage_url", source),
        **meta,
        "duration": info.get("duration") or meta["duration"],
        "audio_only": False,
    }


def grab_thumbnail(video_path: str, t: float, out_path: str) -> str:
    """One 9:16-ish frame at time `t` from a local file — used for clip previews before rendering."""
    cmd = [FFMPEG_EXE, "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video_path,
           "-frames:v", "1", "-vf", "scale=360:640:force_original_aspect_ratio=increase,crop=360:640",
           "-q:v", "3", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"Could not grab a preview frame: {r.stderr.strip()[-200:]}")
    return out_path


def extract_audio(video_path: str, out_path: str, start: float = 0, end: float = 0) -> str:
    """16 kHz mono WAV for Whisper + loudness analysis. Optional time range."""
    cmd = [FFMPEG_EXE, "-y", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", video_path]
    if end and end > start:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {r.stderr.strip()[-300:]}")
    return out_path
