#!/usr/bin/env python3
"""Captioned, incremental GPU renderer for the ClipCraft Kaggle worker."""

import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    import yt_dlp
except ImportError as exc:
    raise RuntimeError("Install yt-dlp before starting the ClipCraft worker.") from exc


BASE_DIR = "/kaggle/working" if os.path.isdir("/kaggle") else os.path.abspath(".")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")
RAM_DISK_DIR = "/dev/shm/clipcraft" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else os.path.join(BASE_DIR, "tmp")
os.makedirs(OUTPUTS_DIR, exist_ok=True)
os.makedirs(RAM_DISK_DIR, exist_ok=True)
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    try:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("Install ffmpeg or imageio-ffmpeg before starting the worker.") from exc

app = Flask(__name__)
CORS(app)
JOBS = {}
JOBS_LOCK = threading.RLock()
CAPABILITIES = [
    "clipcraft-render-v2",
    "word-captions",
    "incremental-results",
    "clip-metadata-merge",
    "fit-layout",
    "fill-layout",
]

LLM_MODEL_ID = os.environ.get("CLIPCRAFT_LLM_MODEL", "").strip()
LLM_API_KEY = os.environ.get("CLIPCRAFT_LLM_API_KEY", "").strip()
LLM_MODEL = None
LLM_TOKENIZER = None
LLM_LOCK = threading.Lock()


def _hardware():
    gpus = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [item.strip() for item in line.split(",")]
                if len(parts) >= 3:
                    gpus.append({"id": int(parts[0]), "name": parts[1], "memory_mb": float(parts[2])})
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as stream:
            total_kb = next(int(line.split()[1]) for line in stream if line.startswith("MemTotal:"))
        ram_gb = round(total_kb / (1024 * 1024), 1)
    except (OSError, StopIteration, ValueError):
        ram_gb = 0.0
    return {
        "gpu_count": len(gpus),
        "gpus": gpus,
        "ram_total_gb": ram_gb,
        "is_kaggle": os.path.isdir("/kaggle"),
        "engine": f"ClipCraft NVENC worker ({len(gpus)} GPU{'s' if len(gpus) != 1 else ''})",
    }


def _load_llm():
    """Load an optional quantized open model for OpenAI-compatible ranking calls."""
    global LLM_MODEL, LLM_TOKENIZER, LLM_API_KEY
    if not LLM_MODEL_ID:
        return
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise RuntimeError(
            "Install torch, transformers, accelerate, and bitsandbytes to enable the Kaggle LLM."
        ) from exc
    if not torch.cuda.is_available():
        raise RuntimeError("The Kaggle LLM requires a GPU-enabled notebook session.")

    if not LLM_API_KEY:
        LLM_API_KEY = secrets.token_urlsafe(32)
    print(f"[llm] Loading {LLM_MODEL_ID} in 4-bit mode...", flush=True)
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    LLM_TOKENIZER = AutoTokenizer.from_pretrained(LLM_MODEL_ID)
    LLM_MODEL = AutoModelForCausalLM.from_pretrained(
        LLM_MODEL_ID,
        quantization_config=quantization,
        torch_dtype=torch.float16,
        device_map="auto",
        low_cpu_mem_usage=True,
    )
    print(f"[llm] Ready: {LLM_MODEL_ID}", flush=True)
    print(f"[llm] API key (copy into ClipCraft Settings): {LLM_API_KEY}", flush=True)


def _llm_authorized():
    token = request.headers.get("Authorization", "")
    return bool(LLM_API_KEY and secrets.compare_digest(token, f"Bearer {LLM_API_KEY}"))


def _safe_id(value):
    value = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("Invalid job or clip id.")
    return value


def _formats(source):
    if os.path.isfile(source):
        raise ValueError("Cloud rendering requires a YouTube URL; local file paths are not available to Kaggle.")
    with yt_dlp.YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/best",
    }) as ydl:
        info = ydl.extract_info(source, download=False)
    formats = info.get("requested_formats") or [info]
    if not formats or not formats[0].get("url"):
        raise RuntimeError("YouTube did not provide a downloadable video stream.")
    return info, formats


def _download_section(formats, start, end, path):
    args = [FFMPEG, "-y", "-v", "error"]
    for fmt in formats:
        headers = fmt.get("http_headers") or {}
        if headers:
            args += ["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())]
        args += ["-ss", f"{start:.3f}", "-i", fmt["url"]]
    args += ["-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", f"{len(formats) - 1}:a:0?",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", path]
    result = subprocess.run(args, capture_output=True, text=True, timeout=75)
    if result.returncode != 0 or not os.path.isfile(path) or os.path.getsize(path) < 10_000:
        detail = (result.stderr or "empty segment").strip()[-400:]
        raise RuntimeError(f"Could not fetch this clip's source segment: {detail}")


def _ass_time(seconds):
    ticks = max(0, int(round(seconds * 100)))
    return f"{ticks // 360000}:{ticks // 6000 % 60:02d}:{ticks // 100 % 60:02d}.{ticks % 100:02d}"


def _srt_time(seconds):
    millis = max(0, int(round(seconds * 1000)))
    return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d},{millis % 1000:03d}"


def _clean_text(value):
    return str(value or "").replace("\\", "").replace("{", "(").replace("}", ")").replace("\r", " ").replace("\n", " ")


def _ass_color(hex_color):
    value = str(hex_color or "#c8ff64").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "c8ff64"
    return f"&H00{value[4:6]}{value[2:4]}{value[0:2]}&"


def _write_captions(words, clip, options, ass_path):
    clip_start = float(clip["start_time"])
    clip_end = float(clip["end_time"])
    duration = clip_end - clip_start
    selected = [
        {"word": _clean_text(item.get("word", "")),
         "start": max(0.0, float(item["start"]) - clip_start),
         "end": min(duration, float(item["end"]) - clip_start)}
        for item in words
        if item.get("word") and float(item.get("end", 0)) > clip_start
        and float(item.get("start", 0)) < clip_end
        and float(item.get("end", 0)) > float(item.get("start", 0))
    ]
    groups, current = [], []
    for word in selected:
        current_text = " ".join(item["word"] for item in current)
        if current and (word["start"] - current[-1]["end"] > 0.7
                        or len(current_text) + len(word["word"]) > 26):
            groups.append(current)
            current = []
        current.append(word)
        if len(current) >= 4 or re.search(r"[.!?][\"']?$", word["word"]):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    style = options.get("caption_style", "bold")
    font, size, outline = (("Arial", 68, 5) if style == "bold" else
                           ("DejaVu Sans", 58, 3) if style == "clean" else
                           ("Arial", 52, 2) if style == "minimal" else
                           ("Arial", 82, 7))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,75,75,440,1
Style: Brand,Arial,32,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,3,12,0,7,60,60,100,1
Style: Title,Arial,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,5,2,8,90,90,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []

    def add(start, end, name, text, layer=0):
        if end > start:
            lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{name},,0,0,0,,{text}")

    if options.get("enable_subtitles", True):
        active_color = _ass_color(options.get("caption_color", "#c8ff64"))
        for group_index, group in enumerate(groups):
            for word_index, word in enumerate(group):
                next_time = (group[word_index + 1]["start"] if word_index + 1 < len(group) else
                             groups[group_index + 1][0]["start"] if group_index + 1 < len(groups) else duration)
                finish = min(duration, next_time, max(word["end"], min(next_time, word["end"] + 0.25)))
                if style == "minimal":
                    text = " ".join(item["word"] for item in group)
                else:
                    pieces = []
                    for index, item in enumerate(group):
                        token = item["word"].upper() if style == "bold" else item["word"]
                        pieces.append((r"{\c" + active_color + "}" + token + r"{\r}") if index == word_index else token)
                    text = " ".join(pieces)
                add(word["start"], finish, "Caption", text)

    brand = str(options.get("brand_name", "")) if options.get("enable_branding") else ""
    if brand:
        add(0, duration, "Brand", _clean_text(brand), 1)
    title = str(clip.get("title", ""))[:90] if options.get("enable_subtitles", True) else ""
    if title:
        add(0, min(3.0, duration), "Title", _clean_text(title), 1)
    with open(ass_path, "w", encoding="utf-8") as stream:
        stream.write(header + "\n".join(lines) + "\n")

    srt_path = os.path.splitext(ass_path)[0] + ".srt"
    with open(srt_path, "w", encoding="utf-8") as stream:
        for index, group in enumerate(groups, start=1):
            stream.write(f"{index}\n{_srt_time(group[0]['start'])} --> {_srt_time(group[-1]['end'])}\n")
            stream.write(" ".join(item["word"] for item in group) + "\n\n")
    return srt_path


def _filter_path(path):
    return os.path.abspath(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _render_one(job_id, clip, words, options, formats, gpu_id):
    clip_id = _safe_id(clip.get("clip_id") or clip.get("id"))
    start, end = float(clip["start_time"]), float(clip["end_time"])
    duration = end - start
    if not 5 <= duration <= 180:
        raise ValueError("Clip duration must be between 5 and 180 seconds.")
    layout = options.get("crop_mode", "fit")
    if layout not in ("fit", "auto", "fill"):
        raise ValueError("Cloud rendering currently supports Fit and Fill layouts.")
    layout = "fit" if layout == "auto" else layout

    prefix = f"{job_id}_{clip_id}"
    video_name = f"{prefix}.mp4"
    thumb_name = f"{prefix}.jpg"
    ass_path = os.path.join(OUTPUTS_DIR, f"{prefix}.ass")
    out_path = os.path.join(OUTPUTS_DIR, video_name)
    thumb_path = os.path.join(OUTPUTS_DIR, thumb_name)
    s0, s1 = max(0.0, start - 1.0), end + 0.5
    fd, section_path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".mp4", dir=RAM_DISK_DIR)
    os.close(fd)
    try:
        _download_section(formats, s0, s1, section_path)
        srt_path = _write_captions(words, clip, options, ass_path)
        section_start = max(0.0, start - s0)
        if layout == "fit":
            graph = (
                "[0:v]setpts=PTS-STARTPTS,split=2[bg0][fg0];"
                "[bg0]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,"
                "boxblur=5:1,eq=brightness=-0.06:saturation=1.08,scale=1080:1920[bg];"
                "[fg0]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[layout]"
            )
        else:
            graph = (
                "[0:v]setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1[layout]"
            )
        ass_path_filter = _filter_path(ass_path)
        if options.get("enable_subtitles", True) or options.get("enable_branding"):
            graph += f";[layout]ass='{ass_path_filter}',format=yuv420p[v]"
        else:
            graph += ";[layout]format=yuv420p[v]"
        command = [
            FFMPEG, "-y", "-v", "error", "-ss", f"{section_start:.3f}", "-i", section_path,
            "-t", f"{duration:.3f}", "-filter_complex_threads", "2", "-filter_complex", graph,
            "-map", "[v]", "-map", "0:a:0?", "-af", "asetpts=PTS-STARTPTS",
            "-r", "30", "-c:v", "h264_nvenc", "-gpu", str(gpu_id),
            "-preset", "p1", "-rc", "vbr", "-cq", "23", "-b:v", "0",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=100)
        if result.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 50_000:
            raise RuntimeError(f"GPU encode failed: {(result.stderr or 'empty output').strip()[-500:]}")
        thumb = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-ss", str(min(1.0, duration / 3)), "-i", out_path,
             "-frames:v", "1", "-vf", "scale=360:640", "-q:v", "3", thumb_path],
            capture_output=True, text=True, timeout=20,
        )
        if thumb.returncode != 0 or not os.path.isfile(thumb_path):
            thumb_name = ""
        return {
            "clip_id": clip_id,
            "video_file": video_name,
            "thumb_file": thumb_name,
            "subtitle_file": os.path.basename(srt_path) if options.get("enable_subtitles", True) else "",
            "layout": layout,
            "status": "ready",
        }
    finally:
        for path in (section_path, ass_path):
            try:
                os.remove(path)
            except OSError:
                pass


def _log(job_id, message, level="info"):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["logs"].append({"t": time.time(), "level": level, "msg": str(message)[:300]})
            del job["logs"][:-100]


def _run_job(job_id, source, clips, words, options):
    started = time.monotonic()
    try:
        _, formats = _formats(source)
        hardware = _hardware()
        gpu_count = hardware["gpu_count"]
        if gpu_count < 1:
            raise RuntimeError("No NVIDIA GPU is available in this Kaggle session.")
        workers = min(6 if gpu_count >= 2 else 3, len(clips))
        _log(job_id, f"Rendering {len(clips)} clips on {gpu_count} NVENC GPU(s), {workers} concurrent jobs.")
        sorted_words = sorted(words, key=lambda item: float(item.get("start", 0)))
        results = []
        errors = []
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for index, clip in enumerate(clips):
                if not isinstance(clip, dict):
                    raise ValueError("Each clip must be an object.")
                _safe_id(clip.get("clip_id") or clip.get("id"))
                start, end = float(clip["start_time"]), float(clip["end_time"])
                # Clips arrive ranked by quality rather than by timestamp. A moving
                # cursor drops captions for a later-ranked clip that happens earlier
                # in the source video, so select the small relevant slice independently.
                clip_words = [
                    word for word in sorted_words
                    if float(word.get("end", 0)) > start and float(word.get("start", 0)) < end
                ]
                futures[pool.submit(_render_one, job_id, clip, clip_words, options, formats, index % gpu_count)] = clip
            for future in as_completed(futures):
                clip = futures[future]
                try:
                    ready = future.result()
                    results.append(ready)
                    with JOBS_LOCK:
                        job = JOBS[job_id]
                        job["clips"].append(ready)
                        done += 1
                        job["progress_pct"] = round(done * 100 / len(clips), 1)
                        job["status_message"] = f"Cloud clips ready: {done}/{len(clips)}"
                    _log(job_id, f"Clip ready: {ready['clip_id']}")
                except Exception as exc:
                    errors.append(f"{clip.get('clip_id', '')}: {str(exc)[:200]}")
                    _log(job_id, f"Clip failed ({clip.get('clip_id', '')}): {str(exc)[:200]}", "error")
        with JOBS_LOCK:
            job = JOBS[job_id]
            job["errors"] = errors
            job["elapsed_seconds"] = round(time.monotonic() - started, 1)
            job["status"] = "completed" if not errors else "failed"
            job["progress_pct"] = 100.0 if not errors else round(len(results) * 100 / len(clips), 1)
            job["status_message"] = (
                f"All {len(results)} cloud clips are ready." if not errors
                else f"{len(results)}/{len(clips)} cloud clips ready; {len(errors)} failed."
            )
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update(status="failed", error=str(exc)[:400],
                           status_message=f"Cloud rendering failed: {str(exc)[:160]}")
        _log(job_id, str(exc), "error")


@app.get("/")
def home():
    return jsonify(status="ok", engine=_hardware()["engine"], capabilities=CAPABILITIES)


@app.get("/health")
def health():
    capabilities = list(CAPABILITIES)
    if LLM_MODEL is not None:
        capabilities.append("openai-compatible-llm")
    return jsonify({
        **_hardware(), "status": "ok", "capabilities": capabilities,
        "llm_model": LLM_MODEL_ID if LLM_MODEL is not None else "",
    })


@app.get("/v1/models")
def llm_models():
    if LLM_MODEL is None:
        return jsonify(error={"message": "The Kaggle LLM is not enabled."}), 503
    if not _llm_authorized():
        return jsonify(error={"message": "Unauthorized."}), 401
    return jsonify(object="list", data=[{"id": LLM_MODEL_ID, "object": "model", "owned_by": "clipcraft"}])


@app.post("/v1/chat/completions")
def llm_chat_completions():
    if LLM_MODEL is None:
        return jsonify(error={"message": "The Kaggle LLM is not enabled."}), 503
    if not _llm_authorized():
        return jsonify(error={"message": "Unauthorized."}), 401
    data = request.get_json(silent=True) or {}
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify(error={"message": "messages must be a non-empty array."}), 400
    try:
        import torch
        normalized = []
        for item in messages:
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                raise ValueError("Each message needs a role and text content.")
            role = str(item.get("role") or "user")
            if role not in ("system", "user", "assistant"):
                continue
            normalized.append({"role": role, "content": item["content"]})
        rendered = LLM_TOKENIZER.apply_chat_template(
            normalized, tokenize=False, add_generation_prompt=True,
        )
        encoded = LLM_TOKENIZER(rendered, return_tensors="pt")
        input_device = LLM_MODEL.get_input_embeddings().weight.device
        encoded = {key: value.to(input_device) for key, value in encoded.items()}
        prompt_tokens = int(encoded["input_ids"].shape[-1])
        if prompt_tokens > 30_000:
            return jsonify(error={"message": "Prompt exceeds the 30,000-token Kaggle model limit."}), 413

        max_new_tokens = min(1600, max(64, int(data.get("max_tokens") or 1200)))
        temperature = max(0.0, min(1.5, float(data.get("temperature", 0.4))))
        generation = {
            **encoded,
            "max_new_tokens": max_new_tokens,
            "do_sample": temperature > 0,
            "pad_token_id": LLM_TOKENIZER.pad_token_id or LLM_TOKENIZER.eos_token_id,
        }
        if temperature > 0:
            generation["temperature"] = max(0.01, temperature)
        with LLM_LOCK, torch.inference_mode():
            output = LLM_MODEL.generate(**generation)
        generated_ids = output[0, prompt_tokens:]
        content = LLM_TOKENIZER.decode(generated_ids, skip_special_tokens=True).strip()
        completion_tokens = int(generated_ids.shape[-1])
        return jsonify({
            "id": f"chatcmpl-{secrets.token_hex(12)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(data.get("model") or LLM_MODEL_ID),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "length" if completion_tokens >= max_new_tokens else "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens},
        })
    except (TypeError, ValueError) as exc:
        return jsonify(error={"message": str(exc)[:300]}), 400
    except Exception as exc:
        print(f"[llm] inference error: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        return jsonify(error={"message": "Kaggle model inference failed."}), 500


@app.post("/api/render_batch")
def render_batch():
    data = request.get_json(silent=True) or {}
    try:
        job_id = _safe_id(data.get("job_id"))
        source = str(data.get("source", "")).strip()
        clips = data.get("clips") or []
        words = data.get("words") or []
        options = data.get("options") or {}
        if not source or not clips:
            raise ValueError("A YouTube URL and at least one clip are required.")
        if not isinstance(clips, list) or not isinstance(words, list) or not isinstance(options, dict):
            raise ValueError("Invalid caption words or render options.")
        if len(clips) > 24:
            raise ValueError("A cloud batch may contain at most 24 clips.")
        for clip in clips:
            if not isinstance(clip, dict):
                raise ValueError("Each clip must be an object.")
            _safe_id(clip.get("clip_id") or clip.get("id"))
            start, end = float(clip["start_time"]), float(clip["end_time"])
            if not (0 <= start < end and 5 <= end - start <= 180):
                raise ValueError("Each clip must be 5 to 180 seconds long.")
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400

    with JOBS_LOCK:
        previous = JOBS.get(job_id)
        if previous and previous["status"] == "processing":
            return jsonify(error="This job is already rendering."), 409
        JOBS[job_id] = {
            "job_id": job_id, "status": "processing", "progress_pct": 0,
            "status_message": f"Preparing {len(clips)} clips on cloud GPU...",
            "clips": [], "logs": [], "errors": [], "start_time": time.time(),
        }
    threading.Thread(target=_run_job, args=(job_id, source, clips, words, options), daemon=True).start()
    return jsonify(status="accepted", job_id=job_id), 202


@app.get("/api/jobs/<job_id>/status")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Job not found."), 404
        return jsonify(dict(job))


@app.get("/api/clips/<job_id>/<filename>")
def download_clip_file(job_id, filename):
    try:
        _safe_id(job_id)
    except ValueError:
        abort(404)
    if os.path.basename(filename) != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        abort(404)
    return send_from_directory(OUTPUTS_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    _load_llm()
    hardware = _hardware()
    print(f"ClipCraft cloud renderer: {hardware['engine']}; capabilities={','.join(CAPABILITIES)}", flush=True)
    app.run(host="0.0.0.0", port=8000, threaded=True)
