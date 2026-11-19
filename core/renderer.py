import os
import subprocess
import tempfile
import threading
import time
from .config import FFMPEG_EXE, OUTPUTS_DIR, OUT_W, OUT_H
from .face_tracker import analyze_video_speakers, crop_trajectory
from .subtitle_engine import generate_karaoke_ass

_encoder = None
_encoder_lock = threading.Lock()


def _video_encoder():
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            r = subprocess.run([FFMPEG_EXE, "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=0.1",
                                "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
            _encoder = "h264_nvenc" if r.returncode == 0 else "libx264"
        return _encoder


def _escape_path(path):
    return os.path.abspath(path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")


def _crop_size(w, h, aspect):
    cw, ch = (h * aspect, h) if w / h > aspect else (w, w / aspect)
    return max(2, int(cw) // 2 * 2), max(2, int(ch) // 2 * 2)


def _tracking_commands(info, cw, ch, which, filter_name):
    points = crop_trajectory(info, cw, ch, which)
    result = []
    for i, (t, x, y, cut) in enumerate(points):
        if i == 0 or cut:
            result.append((t, f"{filter_name} x {x}, {filter_name} y {y}"))
            continue
        pt, px, py, _ = points[i - 1]
        if px == x and py == y:
            continue
        steps = max(1, int((t - pt) / 0.1))
        for step in range(1, steps + 1):
            frac = step / steps
            xx, yy = int(px + (x - px) * frac) // 2 * 2, int(py + (y - py) * frac) // 2 * 2
            result.append((pt + (t - pt) * frac, f"{filter_name} x {xx}, {filter_name} y {yy}"))
    return result, points[0][1:3]


def _layout_filter(info, mode, cmd_path):
    w, h = info["frame_width"], info["frame_height"]
    commands = []
    prefix = "[0:v]setpts=PTS-STARTPTS"
    if mode == "fit":
        graph = (prefix + ",split=2[bg0][fg0];"
                 f"[bg0]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},gblur=sigma=30[bg];"
                 f"[fg0]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fg];"
                 "[bg][fg]overlay=(W-w)/2:(H-h)/2[layout]")
    elif mode == "split":
        cw, ch = _crop_size(w, h, OUT_W / (OUT_H / 2))
        left, (lx, ly) = _tracking_commands(info, cw, ch, "left", "crop@left")
        right, (rx, ry) = _tracking_commands(info, cw, ch, "right", "crop@right")
        commands = left + right
        graph = (prefix + f",sendcmd=f='{_escape_path(cmd_path)}',split=2[left][right];"
                 f"[left]crop@left={cw}:{ch}:{lx}:{ly},scale={OUT_W}:{OUT_H // 2}[top];"
                 f"[right]crop@right={cw}:{ch}:{rx}:{ry},scale={OUT_W}:{OUT_H // 2}[bottom];"
                 "[top][bottom]vstack=inputs=2[layout]")
    elif mode == "gameplay":
        cw = max(2, int(min(w, h * 0.65)) // 2 * 2)
        ch = max(2, int(cw / (OUT_W / 640)) // 2 * 2)
        commands, (x, y) = _tracking_commands(info, cw, ch, "primary", "crop@face")
        graph = (prefix + f",sendcmd=f='{_escape_path(cmd_path)}',split=2[face][game];"
                 f"[face]crop@face={cw}:{ch}:{x}:{y},scale={OUT_W}:640[top];"
                 f"[game]scale={OUT_W}:1280:force_original_aspect_ratio=increase,crop={OUT_W}:1280[bottom];"
                 "[top][bottom]vstack=inputs=2[layout]")
    else:
        cw, ch = _crop_size(w, h, OUT_W / OUT_H)
        commands, (x, y) = _tracking_commands(info, cw, ch, "primary", "crop@follow")
        graph = (prefix + f",sendcmd=f='{_escape_path(cmd_path)}',crop@follow={cw}:{ch}:{x}:{y},"
                 f"scale={OUT_W}:{OUT_H}[layout]")
    if commands:
        commands.sort(key=lambda p: p[0])
        with open(cmd_path, "w", encoding="utf-8") as f:
            f.write("\n".join(f"{t:.3f} {command};" for t, command in commands))
    return graph


def _encode(cmd, duration, callback):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")
        last = 0.0
        for line in proc.stdout:
            if line.startswith("out_time_us="):
                try:
                    pct = min(100, int(line.strip().split("=")[1]) / 1_000_000 / max(0.1, duration) * 100)
                except ValueError:
                    continue
                if callback and time.monotonic() - last > 0.7:
                    callback(pct)
                    last = time.monotonic()
        proc.wait()
        errors.seek(0)
        return proc.returncode, errors.read()[-2000:]


def render_clip(video_path, clip, words, job_id, options, progress_callback=None):
    global _encoder
    prefix = f"{job_id}_{clip['id']}"
    output = os.path.join(OUTPUTS_DIR, prefix + ".mp4")
    ass = os.path.join(OUTPUTS_DIR, prefix + ".ass")
    cmd_path = os.path.join(OUTPUTS_DIR, prefix + "_track.txt")
    start, end = clip["start_time"], clip["end_time"]
    duration = end - start
    info = analyze_video_speakers(video_path, start, end,
                                 progress_callback=lambda p: progress_callback(p * 0.15) if progress_callback else None)
    mode = info["mode"] if options.get("crop_mode", "auto") == "auto" else options["crop_mode"]
    graph = _layout_filter(info, mode, cmd_path)
    generate_karaoke_ass(words, start, end, ass, style=options.get("caption_style", "bold"),
                         color=options.get("caption_color", "#c8ff64"),
                         enable_subtitles=options.get("enable_subtitles", True),
                         brand_name=options.get("brand_name", "") if options.get("enable_branding") else "",
                         title=clip.get("title", "") if options.get("enable_subtitles", True) else "")
    graph += f";[layout]setsar=1,ass='{_escape_path(ass)}',format=yuv420p[v]"
    base = [FFMPEG_EXE, "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", video_path,
            "-t", f"{duration:.3f}", "-filter_complex_threads", "2", "-filter_complex", graph,
            "-map", "[v]", "-map", "0:a:0?", "-af", "asetpts=PTS-STARTPTS",
            "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    encoder = _video_encoder()
    cb = (lambda p: progress_callback(15 + p * 0.85)) if progress_callback else None
    for attempt in range(2):
        encoding = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
                    if encoder == "h264_nvenc" else ["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-threads", "4"])
        code, error = _encode(base + encoding + ["-progress", "pipe:1", "-nostats", output], duration, cb)
        if code == 0:
            break
        if encoder == "h264_nvenc" and "nvenc" in error.lower():
            encoder = _encoder = "libx264"
        else:
            raise RuntimeError(f"Video render failed: {error.strip()[-600:]}")
    else:
        raise RuntimeError(f"Video render failed: {error.strip()[-600:]}")

    thumb = os.path.join(OUTPUTS_DIR, prefix + ".jpg")
    subprocess.run([FFMPEG_EXE, "-y", "-v", "error", "-ss", str(min(1, duration / 3)), "-i", output,
                    "-frames:v", "1", "-vf", "scale=360:640", "-q:v", "3", thumb], capture_output=True)
    if os.path.isfile(cmd_path):
        os.remove(cmd_path)
    return {"video_file": os.path.basename(output), "video_url": f"/video/{prefix}.mp4",
            "thumb_url": f"/media/{prefix}.jpg" if os.path.isfile(thumb) else "",
            "subtitle_url": f"/download/{prefix}.srt", "layout": mode}
