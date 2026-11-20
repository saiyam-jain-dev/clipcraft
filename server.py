import os
import re
import math
import uuid
import json
import base64
from urllib.parse import urlsplit
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from core.config import OUTPUTS_DIR, UPLOADS_DIR, load_settings, save_settings, public_settings
from core.jobs import create_job, get_job, list_jobs, delete_job, enqueue, update_job, replace_clip
from core.pipeline import run_clipcraft_pipeline, rerender_clip, render_pending_clip, render_all_pending
from core.downloader import get_video_info
from core.publisher_engine import generate_social_post_package
from core.llm import describe_llm
from core.youtube_uploader import (
    get_youtube_service,
    upload_to_youtube,
    get_client_credentials,
    save_client_credentials,
    get_auth_url,
    exchange_code_for_token,
    disconnect_youtube,
    get_channel_profile
)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app, resources={r"/*": {"origins": "*"}})
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

# Disable caching for development
@app.after_request
def add_cache_headers(response):
    if request.path.startswith('/static/') or request.path == '/':
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response
SUPPORTED_HOSTS = {"youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "rumble.com", "facebook.com", "fb.watch",
                   "twitter.com", "x.com", "loom.com", "drive.google.com", "dailymotion.com", "dai.ly"}


def _error(message, status=400):
    return jsonify(success=False, error=message), status


@app.before_request
def protect_local_server():
    # Disable security checks for development
    pass


def _video_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Paste a valid video link.")
    value = value.strip()
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError("Use a public http/https video link, or upload a file.")
    if not any(host == h or host.endswith("." + h) for h in SUPPORTED_HOSTS):
        raise ValueError("Unsupported video host. Use YouTube, Vimeo, Twitch, Drive, or upload the video file.")
    return value


def _number(data, key, default, low, high, integer=False):
    try:
        n = float(data.get(key, default))
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {key.replace('_', ' ')}.")
    if not math.isfinite(n) or not low <= n <= high or (integer and n != int(n)):
        raise ValueError(f"{key.replace('_', ' ').capitalize()} must be between {low} and {high}.")
    return int(n) if integer else n


def _boolean(data, key, default):
    v = data.get(key, default)
    if isinstance(v, bool):
        return v
    if v in ("true", "1", 1):
        return True
    if v in ("false", "0", 0):
        return False
    raise ValueError(f"Invalid {key.replace('_', ' ')}.")


def _options(data):
    settings = load_settings()
    options = {
        "crop_mode": data.get("crop_mode", "fit"),
        "target_clips": _number(data, "target_clips", 5, 1, 24, True),
        "min_duration": _number(data, "min_duration", 15, 8, 180),
        "max_duration": _number(data, "max_duration", 60, 10, 180),
        "range_start": _number(data, "range_start", 0, 0, 86400),
        "range_end": _number(data, "range_end", 0, 0, 86400),
        "enable_subtitles": _boolean(data, "enable_subtitles", True),
        "enable_branding": _boolean(data, "enable_branding", False),
        "brand_name": str(data.get("brand_name", settings["brand_name"]))[:80].strip(),
        "caption_style": data.get("caption_style") or "bold",
        "caption_color": data.get("caption_color") or "#c8ff64",
        "prompt": str(data.get("prompt", ""))[:2000].strip(),
        "language": str(data.get("language", "")).strip(),
        "focus": str(data.get("focus", "auto")),
        "mode": str(data.get("mode", data.get("processing_mode", "fast"))),
    }
    if options["crop_mode"] not in ("auto", "fill", "fit", "split", "gameplay",
                                    "pip_tl", "pip_tr", "pip_bl", "pip_br", "circle", "side"):
        options["crop_mode"] = "fit"
    if options["focus"] not in ("auto", "story", "highlights"):
        options["focus"] = "auto"
    if options["mode"] not in ("fast", "deep"):
        options["mode"] = "fast"
    if options["caption_style"] not in ("bold", "clean", "minimal", "pop"):
        options["caption_style"] = "bold"
    if not isinstance(options["caption_color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", options["caption_color"]):
        options["caption_color"] = "#c8ff64"
    if options["min_duration"] > options["max_duration"]:
        raise ValueError("Minimum clip duration must be less than or equal to maximum.")
    if options["range_end"] and options["range_end"] <= options["range_start"]:
        raise ValueError("End time must be after start time.")
    if options["language"] and not re.fullmatch(r"[a-z]{2,3}|hinglish", options["language"]):
        raise ValueError("Invalid language code.")
    return options


def _start_job(source, options, label):
    job_id = uuid.uuid4().hex[:10]
    create_job({"job_id": job_id, "source": source, "source_label": label, "options": options,
                "status": "queued", "stage": "download", "progress_pct": 0,
                "status_message": "Queued for processing...", "clips": []})
    enqueue(job_id, run_clipcraft_pipeline, source, options)
    return jsonify(success=True, job_id=job_id)


def _lookup(job_id):
    if not re.fullmatch(r"[0-9a-f]{10}", job_id):
        return None
    return get_job(job_id)


@app.route("/")
def index():
    return jsonify(
        name="ClipCraft API",
        version="1.0",
        docs="Next.js frontend runs on http://localhost:3000",
        endpoints=["/api/health", "/api/video-info", "/api/clip", "/api/jobs", "/api/settings"]
    )


@app.get("/api/health")
def health():
    s = load_settings()
    return jsonify(success=True, engine=describe_llm(s), whisper_model=s["whisper_model"])


@app.get("/api/video-info")
def video_info():
    try:
        url = _video_url(request.args.get("url", ""))
        return jsonify(success=True, info=get_video_info(url))
    except ValueError as e:
        return _error(str(e))
    except RuntimeError as e:
        return _error(str(e), 422)


@app.post("/api/clip")
def start_clip():
    data = request.get_json() if request.is_json else request.form
    if not isinstance(data, (dict, type(request.form))):
        return _error("Expected a JSON object or form.")
    try:
        options = _options(data)
        upload = request.files.get("video_file")
        if upload and upload.filename:
            name = secure_filename(upload.filename)
            if not name or os.path.splitext(name)[1].lower() not in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"):
                raise ValueError("Upload an MP4, MOV, MKV, WebM, AVI, or M4V video.")
            source = os.path.join(UPLOADS_DIR, f"{uuid.uuid4().hex[:8]}_{name}")
            upload.save(source)
            label = upload.filename[:200]
        else:
            source = _video_url(data.get("url", ""))
            label = source
    except ValueError as e:
        return _error(str(e))
    return _start_job(source, options, label)


@app.get("/api/jobs")
def jobs():
    return jsonify(success=True, jobs=list_jobs())


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    comp_file = os.path.join(OUTPUTS_DIR, f"{job_id}_compilation.mp4")
    if os.path.exists(comp_file):
        job["compilation_video_url"] = f"/video/{job_id}_compilation.mp4"
    return jsonify(success=True, job=job)


@app.post("/api/jobs/<job_id>/compile")
def compile_job_endpoint(job_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    from core.jobs import generate_compilation_video
    try:
        filename = generate_compilation_video(job_id)
        return jsonify(success=True, compilation_video_url=f"/video/{filename}")
    except Exception as e:
        return _error(str(e))


@app.delete("/api/jobs/<job_id>")
def remove_job(job_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    if not delete_job(job_id):
        return _error("A running project cannot be deleted.", 409)
    return jsonify(success=True)


@app.post("/api/retry/<job_id>")
def retry_job(job_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    if job["status"] in ("queued", "processing"):
        return _error("This project is still running.", 409)
    return _start_job(job["source"], _options(job.get("options", {})), job.get("source_label", job["source"]))


def _clip_in_job(job, clip_id):
    return next((c for c in job.get("clips", []) if c.get("clip_id") == clip_id), None)


@app.post("/api/jobs/<job_id>/clips/<clip_id>/render")
def render_clip_endpoint(job_id, clip_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    if job["status"] in ("queued", "processing"):
        return _error("This project is still running.", 409)
    clip = _clip_in_job(job, clip_id)
    if not clip:
        return _error("Clip not found.", 404)
    if clip.get("video_file"):
        return jsonify(success=True, job_id=job_id, already_rendered=True)
    update_job(job_id, status="queued", stage="render", progress_pct=0,
               status_message="Queued for render...")
    enqueue(job_id, render_pending_clip, clip_id)
    return jsonify(success=True, job_id=job_id)


@app.post("/api/jobs/<job_id>/render-all")
def render_all_clips(job_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    if job["status"] in ("queued", "processing"):
        return _error("This project is still running.", 409)
    if not any(not c.get("video_file") for c in job.get("clips", [])):
        return _error("Every clip is already rendered.", 409)
    update_job(job_id, status="queued", stage="render", progress_pct=0,
               status_message="Queued to render all previews...")
    enqueue(job_id, render_all_pending)
    return jsonify(success=True, job_id=job_id)


@app.post("/api/jobs/<job_id>/clips/<clip_id>/trim")
def trim_clip(job_id, clip_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    if job["status"] in ("queued", "processing"):
        return _error("This project is still running.", 409)
    clip = next((c for c in job.get("clips", []) if c.get("clip_id") == clip_id), None)
    if not clip:
        return _error("Clip not found.", 404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Expected a JSON object.")
    try:
        start = clip["start_time"] + _number(data, "start_shift", 0, -30, 30)
        end = clip["end_time"] + _number(data, "end_shift", 0, -30, 30)
    except ValueError as e:
        return _error(str(e))
    start = max(0.0, start)
    if job.get("video_duration"):
        end = min(end, float(job["video_duration"]))
    if abs(start - clip["start_time"]) < 0.05 and abs(end - clip["end_time"]) < 0.05:
        return _error("Change the start or end by at least 0.1 seconds.")
    if not 5 <= end - start <= 180:
        return _error("A trimmed clip must be between 5 and 180 seconds long.")
    update_job(job_id, status="queued", stage="render", progress_pct=0, status_message="Queued for re-render...")
    enqueue(job_id, rerender_clip, clip_id, round(start, 3), round(end, 3))
    return jsonify(success=True, job_id=job_id)


REEDIT_LAYOUTS = ("auto", "fill", "fit", "split", "gameplay",
                  "pip_tl", "pip_tr", "pip_bl", "pip_br", "circle", "side")
REEDIT_STYLES = ("bold", "clean", "minimal", "pop")


@app.post("/api/jobs/<job_id>/clips/<clip_id>/reedit")
def reedit_clip_endpoint(job_id, clip_id):
    """Re-render a saved clip with a new layout, caption style, or trimmed times —
    no re-analysis, just a fresh render from the cached transcript/footage."""
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    if job["status"] in ("queued", "processing"):
        return _error("This project is still running.", 409)
    clip = _clip_in_job(job, clip_id)
    if not clip:
        return _error("Clip not found.", 404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Expected a JSON object.")
    overrides = {}
    try:
        if "crop_mode" in data:
            if data["crop_mode"] not in REEDIT_LAYOUTS:
                raise ValueError("Invalid layout.")
            overrides["crop_mode"] = data["crop_mode"]
        if "caption_style" in data:
            style = data.get("caption_style") or "bold"
            overrides["caption_style"] = style if style in REEDIT_STYLES else "bold"
        if "caption_color" in data:
            color = str(data.get("caption_color") or "#c8ff64")
            overrides["caption_color"] = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#c8ff64"
        for key in ("enable_subtitles", "enable_branding"):
            if key in data:
                overrides[key] = _boolean(data, key, False)
        if "brand_name" in data:
            overrides["brand_name"] = str(data["brand_name"])[:80].strip()
        start = clip["start_time"] + _number(data, "start_shift", 0, -30, 30)
        end = clip["end_time"] + _number(data, "end_shift", 0, -30, 30)
    except ValueError as e:
        return _error(str(e))
    start = max(0.0, start)
    if job.get("video_duration"):
        end = min(end, float(job["video_duration"]))
    if not 5 <= end - start <= 180:
        return _error("A clip must be between 5 and 180 seconds long.")
    same_time = abs(start - clip["start_time"]) < 0.05 and abs(end - clip["end_time"]) < 0.05
    if same_time and not overrides:
        return _error("Change the layout, captions, or trim points first.")
    update_job(job_id, status="queued", stage="render", progress_pct=0,
               status_message="Queued for re-edit...")
    enqueue(job_id, rerender_clip, clip_id, round(start, 3), round(end, 3), overrides)
    return jsonify(success=True, job_id=job_id)


@app.post("/api/jobs/<job_id>/clips/<clip_id>/feedback")
def clip_feedback(job_id, clip_id):
    """1-5 star rating for a clip. Stored locally; weights refit from all ratings."""
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    clip = _clip_in_job(job, clip_id)
    if not clip:
        return _error("Clip not found.", 404)
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    if not 1 <= rating <= 5:
        return _error("Choose a rating from 1 to 5 stars.")
    from core.feedback import record_feedback
    summary = record_feedback(job_id, clip_id, rating, clip)
    updated = dict(clip)
    updated["rating"] = rating
    replace_clip(job_id, clip_id, updated)
    return jsonify(success=True, rating=rating, feedback=summary)


@app.get("/api/feedback/summary")
def feedback_summary():
    from core.feedback import get_feedback_stats
    return jsonify(success=True, feedback=get_feedback_stats())


@app.get("/video/<filename>")
def stream_video(filename):
    if not filename.endswith(".mp4"):
        return _error("Video not found.", 404)
    return send_from_directory(OUTPUTS_DIR, filename, mimetype="video/mp4", conditional=True)


@app.get("/media/<filename>")
def media(filename):
    if not filename.endswith(".jpg"):
        return _error("Image not found.", 404)
    return send_from_directory(OUTPUTS_DIR, filename, mimetype="image/jpeg")


@app.get("/download/<filename>")
def download(filename):
    if not filename.endswith((".mp4", ".srt")):
        return _error("File not found.", 404)
    return send_from_directory(OUTPUTS_DIR, filename, as_attachment=True, conditional=True)


@app.get("/api/social-package/<job_id>/<clip_id>")
def social_package(job_id, clip_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    clip = next((c for c in job.get("clips", []) if c["clip_id"] == clip_id), None)
    if not clip:
        return _error("Clip not found.", 404)
    brand = job.get("options", {}).get("brand_name", "")
    return jsonify(success=True, package=generate_social_post_package(clip, brand))


@app.get("/api/youtube/auth-status")
def youtube_auth_status():
    creds = get_client_credentials()
    youtube = get_youtube_service()
    channel = get_channel_profile() if youtube else None
    return jsonify(
        success=True,
        configured=creds is not None,
        authenticated=youtube is not None,
        channel=channel
    )


@app.post("/api/youtube/save-credentials")
def youtube_save_credentials():
    data = request.get_json(silent=True) or {}
    cid = (data.get("client_id") or "").strip()
    csecret = (data.get("client_secret") or "").strip()
    raw_json = (data.get("client_secrets_json") or "").strip()

    if raw_json:
        try:
            parsed = json.loads(raw_json)
            client_block = parsed.get("web") or parsed.get("installed") or parsed
            cid = client_block.get("client_id", "").strip()
            csecret = client_block.get("client_secret", "").strip()
        except Exception as e:
            return _error(f"Invalid JSON client secrets format: {e}")

    if not cid or not csecret:
        return _error("Both Client ID and Client Secret are required.")

    try:
        path = save_client_credentials(cid, csecret)
        return jsonify(success=True, message=f"Credentials saved successfully to {path}")
    except Exception as e:
        return _error(f"Failed to save credentials: {e}")


@app.get("/api/youtube/auth-url")
def youtube_auth_url():
    redirect_uri = request.args.get("redirect_uri")
    if not redirect_uri:
        # Default to current host callback or Vercel callback
        redirect_uri = f"{request.host_url.rstrip('/')}/api/youtube/oauth2callback"
    try:
        url = get_auth_url(redirect_uri)
        return jsonify(success=True, auth_url=url, redirect_uri=redirect_uri)
    except Exception as e:
        return _error(str(e))


@app.get("/api/youtube/oauth2callback")
def youtube_oauth2_callback():
    code = request.args.get("code")
    err = request.args.get("error")
    state = request.args.get("state")
    if err:
        return f"<h3>Authentication cancelled or failed: {err}</h3><p><a href='/'>Return to ClipCraft</a></p>", 400
    if not code:
        return "<h3>Missing authorization code.</h3>", 400

    extracted_uri = None
    if state:
        try:
            padded = state + "=" * (-len(state) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
            extracted_uri = data.get("redirect_uri")
        except Exception:
            extracted_uri = None

    candidates = []
    if extracted_uri:
        candidates.append(extracted_uri)

    # Standard candidate redirect URIs in priority order
    candidates.append("https://clipcraft-theta.vercel.app/api/youtube/oauth2callback")
    candidates.append("https://clipcraft-production-d672f3fd.up.railway.app/api/youtube/oauth2callback")

    f_proto = request.headers.get("X-Forwarded-Proto", "https")
    f_host = request.headers.get("X-Forwarded-Host")
    if f_host:
        candidates.append(f"{f_proto}://{f_host}/api/youtube/oauth2callback")
    candidates.append(request.base_url)

    seen = set()
    ordered_uris = [u for u in candidates if u and not (u in seen or seen.add(u))]

    exchanged = False
    last_err = ""
    for r_uri in ordered_uris:
        try:
            exchange_code_for_token(code, r_uri)
            exchanged = True
            break
        except Exception as e:
            last_err = str(e)
            if "redirect_uri_mismatch" not in last_err.lower():
                break

    if not exchanged:
        return f"""
        <!DOCTYPE html>
        <html>
          <head>
            <title>Authorization Failed — ClipCraft</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
              body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                     background: #0c1015; color: #f0f6fc; display: flex; align-items: center;
                     justify-content: center; height: 100vh; margin: 0; text-align: center; }}
              .box {{ background: #161e27; border: 1px solid #e5534b; border-radius: 12px; padding: 32px 40px; max-width: 520px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }}
              h2 {{ color: #e5534b; margin-top: 0; }}
              p {{ color: #a2b2c2; font-size: 14px; margin-bottom: 24px; word-break: break-word; }}
              a {{ color: #c8ff64; text-decoration: none; font-weight: 600; }}
            </style>
          </head>
          <body>
            <div class="box">
              <h2>Authorization Failed</h2>
              <p>{last_err}</p>
              <a href="/">&larr; Return to ClipCraft</a>
            </div>
            <script>
              if (window.opener) {{
                try {{ window.opener.postMessage({{ type: 'YOUTUBE_AUTH_ERROR', error: '{last_err}' }}, '*'); }} catch(e){{}}
              }}
            </script>
          </body>
        </html>
        """, 400

    frontend = os.environ.get("FRONTEND_URL", "https://clipcraft-theta.vercel.app")
    return f"""
    <!DOCTYPE html>
    <html>
      <head>
        <title>YouTube Connected — ClipCraft</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                 background: #0c1015; color: #f0f6fc; display: flex; align-items: center;
                 justify-content: center; height: 100vh; margin: 0; text-align: center; }}
          .box {{ background: #161e27; border: 1px solid #283747; border-radius: 12px; padding: 32px 40px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }}
          h2 {{ color: #c8ff64; margin-top: 0; }}
          p {{ color: #a2b2c2; font-size: 14px; margin-bottom: 24px; }}
          a {{ color: #c8ff64; text-decoration: none; font-weight: 600; }}
        </style>
      </head>
      <body>
        <div class="box">
          <h2>✓ YouTube Channel Connected!</h2>
          <p>You can now return to ClipCraft and publish clips directly as YouTube Shorts.</p>
          <a href="{frontend}/?youtube_connected=true">Continue to Studio &rarr;</a>
        </div>
        <script>
          if (window.opener) {{
            try {{ window.opener.postMessage({{ type: 'YOUTUBE_AUTH_SUCCESS' }}, '*'); }} catch(e){{}}
            setTimeout(function() {{ window.close(); }}, 1800);
          }} else {{
            setTimeout(function() {{ window.location.href = '{frontend}/?youtube_connected=true'; }}, 1800);
          }}
        </script>
      </body>
    </html>
    """


@app.post("/api/youtube/exchange-code")
def youtube_exchange_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    redirect_uri = (data.get("redirect_uri") or "").strip()
    if not code:
        return _error("Authorization code is required.")

    candidates = []
    if redirect_uri:
        candidates.append(redirect_uri)
    candidates.extend([
        "https://clipcraft-theta.vercel.app/api/youtube/oauth2callback",
        "https://clipcraft-production-d672f3fd.up.railway.app/api/youtube/oauth2callback",
        f"{request.host_url.rstrip('/')}/api/youtube/oauth2callback"
    ])
    seen = set()
    ordered = [u for u in candidates if u and not (u in seen or seen.add(u))]

    last_err = ""
    for r_uri in ordered:
        try:
            token_data = exchange_code_for_token(code, r_uri)
            channel = get_channel_profile()
            return jsonify(success=True, channel=channel, message="YouTube connected successfully!", token=token_data)
        except Exception as e:
            last_err = str(e)
            if "redirect_uri_mismatch" not in last_err.lower():
                break
    return _error(f"Failed to exchange code: {last_err}")


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    try:
        disconnect_youtube()
        return jsonify(success=True, message="YouTube disconnected successfully")
    except Exception as e:
        return _error(str(e))


@app.post("/api/youtube/upload/<job_id>/<clip_id>")
def upload_clip_to_youtube(job_id, clip_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)
    clip = next((c for c in job.get("clips", []) if c["clip_id"] == clip_id), None)
    if not clip:
        return _error("Clip not found.", 404)
    if not clip.get("video_file"):
        return _error("Clip must be rendered before uploading.", 400)

    data = request.get_json(silent=True) or {}
    title = data.get("title", clip.get("title", ""))[:100]
    description = data.get("description", clip.get("caption", ""))[:5000]
    tags = data.get("tags", [])[:15]
    privacy = data.get("privacy", "public")

    video_path = os.path.join(OUTPUTS_DIR, clip["video_file"])
    if not os.path.exists(video_path):
        return _error("Video file not found.", 404)

    try:
        result = upload_to_youtube(video_path, title, description, tags, privacy)
        return jsonify(
            success=True,
            video_id=result.get("id"),
            video_url=result.get("url"),
            url=result.get("url")
        )
    except Exception as e:
        return _error(str(e))


@app.post("/api/youtube/upload-all/<job_id>")
def upload_all_clips_to_youtube(job_id):
    job = _lookup(job_id)
    if not job:
        return _error("Project not found.", 404)

    data = request.get_json(silent=True) or {}
    privacy = data.get("privacy", "public")
    selected_cids = set(data.get("clip_ids", []))

    all_rendered = [c for c in job.get("clips", []) if c.get("video_file")]
    if selected_cids:
        clips = [c for c in all_rendered if c["clip_id"] in selected_cids]
    else:
        clips = all_rendered

    if not clips:
        return _error("No rendered clips found to upload. Please render clips first.", 400)

    results = []
    for clip in clips:
        video_path = os.path.join(OUTPUTS_DIR, clip["video_file"])
        if not os.path.exists(video_path):
            results.append({"clip_id": clip["clip_id"], "title": clip.get("title", ""), "success": False, "error": "Video file not found"})
            continue
        try:
            result = upload_to_youtube(
                video_path,
                clip.get("title", "")[:100],
                clip.get("caption", "")[:5000],
                clip.get("hashtags", [])[:15],
                privacy
            )
            vid = result.get("id")
            vurl = result.get("url")
            results.append({
                "clip_id": clip["clip_id"],
                "title": clip.get("title", ""),
                "success": True,
                "video_id": vid,
                "video_url": vurl,
                "url": vurl
            })
        except Exception as e:
            results.append({
                "clip_id": clip["clip_id"],
                "title": clip.get("title", ""),
                "success": False,
                "error": str(e)[:250]
            })

    success_count = sum(1 for r in results if r["success"])
    return jsonify(
        success=True,
        total=len(clips),
        uploaded_count=success_count,
        results=results
    )


@app.route("/api/settings", methods=["GET", "POST"])
def handle_settings():
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("Expected a JSON object.")
        # Strip read-only / set indicator flags sent back by frontend
        cleaned = {k: v for k, v in data.items() if not k.endswith("_set")}
        for key in ("fallback_providers", "proxy_host", "proxy_port", "proxy_user", "proxy_pass"):
            cleaned.pop(key, None)
        # Validate that all editable settings are strings
        for k, v in cleaned.items():
            if not isinstance(v, str) or len(v) > 4096:
                return _error(f"Invalid value for {k}.")
        data = cleaned
        merged = {**load_settings(), **data}
        if merged["llm_provider"] not in ("none", "gemini", "openai_compatible"):
            return _error("Invalid AI provider.")
        if merged["whisper_model"] not in ("tiny", "base", "small", "medium", "large-v3"):
            return _error("Invalid Whisper model.")
        if merged["cookies_from_browser"] not in ("", "chrome", "edge", "firefox", "brave", "opera", "vivaldi"):
            return _error("Invalid browser name.")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", merged["gemini_model"]):
            return _error("Invalid Gemini model.")
        try:
            base = urlsplit(merged["openai_base_url"])
            local = base.hostname in ("localhost", "127.0.0.1", "::1")
            if base.scheme != "https" and not (base.scheme == "http" and local):
                return _error("Use HTTPS for a hosted AI API, or HTTP for a local AI server.")
            if not base.hostname or base.username or base.password or base.query or base.fragment:
                return _error("Invalid AI API base URL.")
        except ValueError:
            return _error("Invalid AI API base URL.")
        save_settings(data)
    return jsonify(success=True, settings=public_settings(), engine=describe_llm(load_settings()))


@app.post("/api/remote_worker/test")
def test_remote_worker_endpoint():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    from core.remote_worker import test_remote_worker
    res = test_remote_worker(url)
    return jsonify(success=True, **res)


@app.get("/api/hardware")
def hardware_endpoint():
    from core.chunk_engine import get_hardware_profile
    import shutil
    hw = get_hardware_profile()
    hw["node_which"] = shutil.which("node")
    hw["nodejs_which"] = shutil.which("nodejs")
    hw["path_env"] = os.environ.get("PATH")
    return jsonify(hw)


@app.get("/api/cli-test")
def cli_test_endpoint():
    url = request.args.get("url", "https://www.youtube.com/live/VZqAPdUvqnQ")
    cookie_mode = request.args.get("cookies", "account")
    from core.downloader import ANON_COOKIES_PATH, ensure_netscape_cookies
    cmd = ["yt-dlp", "-F", "--verbose"]
    import shutil
    deno_bin = shutil.which("deno") or "/usr/local/bin/deno"
    node_bin = shutil.which("node") or "/usr/bin/node"
    if os.path.exists(deno_bin):
        cmd += ["--js-runtimes", f"deno:{deno_bin}"]
    elif os.path.exists(node_bin):
        cmd += ["--js-runtimes", f"node:{node_bin}"]
    if cookie_mode == "anon" and os.path.exists(ANON_COOKIES_PATH):
        cmd += ["--cookies", ANON_COOKIES_PATH]
    elif cookie_mode == "account" and os.path.exists("/tmp/youtube_cookies.txt"):
        ensure_netscape_cookies("/tmp/youtube_cookies.txt")
        cmd += ["--cookies", "/tmp/youtube_cookies.txt"]
    client_param = request.args.get("client")
    if client_param:
        cmd += ["--extractor-args", f"youtube:player_client={client_param}"]
    cmd.append(url)
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return jsonify({
        "cmd": cmd,
        "returncode": r.returncode,
        "stdout": r.stdout[-3000:],
        "stderr": r.stderr[-3000:]
    })


@app.get("/api/test-formats")
def test_formats_endpoint():
    url = request.args.get("url", "https://www.youtube.com/live/VZqAPdUvqnQ")
    use_cookies = request.args.get("cookies", "1") == "1"
    anon_only = request.args.get("anon", "0") == "1"
    client_param = request.args.get("client")
    
    from core.remote_source import _resolve_stream_formats
    from core.downloader import _ydl_opts, ANON_COOKIES_PATH, _extract_with_fallback
    import yt_dlp

    opts = _ydl_opts(skip_download=True)
    if not use_cookies:
        opts.pop("cookiefile", None)
        opts.pop("cookiesfrombrowser", None)
    elif anon_only and os.path.exists(ANON_COOKIES_PATH):
        opts["cookiefile"] = ANON_COOKIES_PATH

    if client_param:
        clients = [c.strip() for c in client_param.split(",")]
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
    
    cookiefile = opts.get("cookiefile")
    cookie_exists = os.path.exists(cookiefile) if cookiefile else False
    cookie_size = os.path.getsize(cookiefile) if cookie_exists else 0
    
    fmts = []
    extract_err = None
    info = {}
    try:
        info = _extract_with_fallback(opts, url, download=False)
        fmts = info.get("formats", [])
    except Exception as e:
        extract_err = str(e)

    resolved = None
    res_err = None
    if info:
        try:
            res = _resolve_stream_formats(info)
            resolved = [{"id": f.get("format_id"), "proto": f.get("protocol"), "vcodec": f.get("vcodec"), "acodec": f.get("acodec")} for f in res]
        except Exception as e:
            res_err = str(e)
    
    summary = [{
        "id": f.get("format_id"),
        "proto": f.get("protocol"),
        "vcodec": f.get("vcodec"),
        "acodec": f.get("acodec"),
        "has_url": bool(f.get("url")),
        "ext": f.get("ext"),
        "res": f.get("resolution")
    } for f in fmts]
    cookie_names = []
    if cookiefile and os.path.exists(cookiefile):
        try:
            with open(cookiefile, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) >= 6:
                        cookie_names.append(parts[5])
        except Exception:
            pass

    anon_names = []
    if os.path.exists(ANON_COOKIES_PATH):
        try:
            with open(ANON_COOKIES_PATH, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) >= 6:
                        anon_names.append(parts[5])
        except Exception:
            pass

    return jsonify({
        "cookiefile": cookiefile,
        "cookie_exists": cookie_exists,
        "cookie_size": cookie_size,
        "cookie_names": cookie_names,
        "anon_names": anon_names,
        "use_cookies": use_cookies,
        "client": client_param or opts.get("extractor_args", {}).get("youtube", {}).get("player_client"),
        "total_formats": len(fmts),
        "extract_err": extract_err,
        "formats": summary,
        "resolved": resolved,
        "res_err": res_err
    })


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(e):
    return _error("File is too large. Maximum upload size is 2 GB.", 413)


@app.errorhandler(404)
def not_found(e):
    return _error("Not found.", 404)


if __name__ == "__main__":
    print("ClipCraft - local AI clipping studio")
    print("Open http://127.0.0.1:5004")
    # Resume any queued job automatically
    for j in list_jobs():
        if j.get("status") == "queued":
            print(f"[queue] Resuming queued project {j['job_id']}: {j.get('source_label', j['source'])}")
            enqueue(j["job_id"], run_clipcraft_pipeline, j["source"], j.get("options", {}))
            break
    app.run(host="0.0.0.0", port=int(os.getenv("CLIPCRAFT_PORT", "5004")), debug=False, threaded=True)
