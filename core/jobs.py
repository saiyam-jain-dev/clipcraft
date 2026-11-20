import os
import re
import json
import time
import glob
import queue
import threading
import traceback
from .config import JOBS_DIR, OUTPUTS_DIR, UPLOADS_DIR

_lock = threading.RLock()
_jobs: dict = {}
_queue: "queue.Queue" = queue.Queue()
_worker_started = False
_pending: list = []


def job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def _persist(job: dict):
    path = job_path(job["job_id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2)
    # Windows indexers/AV briefly lock the target; retry the atomic replace a few times.
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def _load(job_id: str):
    path = job_path(job_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError):
        return None
    # A job left mid-flight by a previous server process will never finish.
    if job.get("status") in ("queued", "processing"):
        job["status"] = "failed"
        job["error"] = "Server restarted while this job was running. Start it again."
        job["status_message"] = job["error"]
        _persist(job)
    return job


def create_job(job: dict) -> dict:
    job.setdefault("created_at", time.time())
    job.setdefault("clips", [])
    with _lock:
        _jobs[job["job_id"]] = job
        _persist(job)
    return job


def get_job(job_id: str):
    with _lock:
        if job_id in _jobs:
            return json.loads(json.dumps(_jobs[job_id]))
        job = _load(job_id)
        if job:
            _jobs[job_id] = job
        return job


def _log(job: dict, msg, level="info"):
    """Activity log shown in the UI. Repeats that differ only in numbers (progress) update in place."""
    log = job.setdefault("log", [])
    msg = str(msg)[:600]
    key = re.sub(r"\d+(?:\.\d+)?", "#", msg)
    if log and log[-1]["level"] == level and re.sub(r"\d+(?:\.\d+)?", "#", log[-1]["msg"]) == key:
        log[-1].update(t=time.time(), msg=msg)
    else:
        log.append({"t": time.time(), "level": level, "msg": msg})
        del log[:-400]


def log_event(job_id: str, msg, level="info", status=False):
    with _lock:
        job = _jobs.get(job_id) or _load(job_id)
        if not job:
            return
        _log(job, msg, level)
        if status:
            job["status_message"] = str(msg)
        job["updated_at"] = time.time()
        _jobs[job_id] = job
        _persist(job)


def update_job(job_id: str, **updates):
    with _lock:
        job = _jobs.get(job_id) or _load(job_id) or {"job_id": job_id}
        if updates.get("error"):
            _log(job, updates["error"], "error")
        elif updates.get("status_message") and updates["status_message"] != job.get("status_message"):
            _log(job, updates["status_message"])
        if updates.get("warning") and updates["warning"] != job.get("warning"):
            _log(job, updates["warning"], "warn")
        job.update(updates)
        job["updated_at"] = time.time()
        _jobs[job_id] = job
        _persist(job)


def append_clip(job_id: str, clip: dict):
    with _lock:
        job = _jobs[job_id]
        job["clips"].append(clip)
        job["clips"].sort(key=lambda c: c.get("virality_score", 0), reverse=True)
        _persist(job)


def replace_clip(job_id: str, clip_id: str, clip: dict):
    with _lock:
        job = _jobs[job_id]
        job["clips"] = [clip if c.get("clip_id") == clip_id else c for c in job["clips"]]
        _persist(job)


def list_jobs() -> list:
    out = []
    for path in glob.glob(os.path.join(JOBS_DIR, "*.json")):
        job_id = os.path.splitext(os.path.basename(path))[0]
        job = get_job(job_id)
        if not job or "source" not in job:
            continue
        clips = job.get("clips", [])
        out.append({
            "job_id": job_id,
            "title": job.get("video_title") or job.get("source_label") or "Untitled video",
            "thumbnail": job.get("video_thumbnail") or (clips[0].get("thumb_url", "") if clips else ""),
            "status": job.get("status"),
            "progress_pct": job.get("progress_pct", 0),
            "clips_count": len(clips),
            "created_at": job.get("created_at", 0),
        })
    out.sort(key=lambda j: j["created_at"], reverse=True)
    return out


def delete_job(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id) or _load(job_id)
        if not job or job.get("status") in ("queued", "processing"):
            return False
        _jobs.pop(job_id, None)
        for p in glob.glob(os.path.join(OUTPUTS_DIR, f"{job_id}_*")):
            try:
                os.remove(p)
            except OSError:
                pass
        if os.path.exists(job_path(job_id)):
            os.remove(job_path(job_id))
        source = os.path.realpath(job.get("source", ""))
        if (os.path.dirname(source) == os.path.realpath(UPLOADS_DIR)
                and re.match(r"^[0-9a-f]{8}_", os.path.basename(source))):
            for path in glob.glob(os.path.join(JOBS_DIR, "*.json")):
                try:
                    with open(path, encoding="utf-8") as f:
                        other = json.load(f)
                    if os.path.realpath(other.get("source", "")) == source:
                        break
                except (OSError, ValueError):
                    break
            else:
                if os.path.isfile(source):
                    os.remove(source)
    return True


def _worker():
    while True:
        job_id, fn, args = _queue.get()
        with _lock:
            if job_id in _pending:
                _pending.remove(job_id)
            for pos, jid in enumerate(_pending, start=1):
                update_job(jid, status_message=f"Waiting in queue (position {pos})...")
        try:
            fn(job_id, *args)
        except Exception as e:  # the pipeline reports its own failures; this is a last resort
            traceback.print_exc()
            update_job(job_id, status="failed", error=str(e), status_message=f"Processing error: {e}")
        finally:
            _queue.task_done()


def enqueue(job_id: str, fn, *args):
    """One job at a time: Whisper + rendering would thrash a single GPU if run in parallel."""
    global _worker_started
    with _lock:
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True).start()
            _worker_started = True
        busy = _queue.unfinished_tasks > 0
        _pending.append(job_id)
        if busy:
            update_job(job_id, status_message=f"Waiting in queue (position {len(_pending)})...")
        _queue.put((job_id, fn, args))


def generate_compilation_video(job_id: str) -> str:
    """Concatenates all rendered clips into a single all-in-one vertical compilation video."""
    import subprocess
    from .config import FFMPEG_EXE
    job = get_job(job_id)
    if not job:
        raise ValueError("Job not found.")
    clips = [c for c in job.get("clips", []) if c.get("video_file")]
    if not clips:
        raise ValueError("No rendered clips found to compile.")
    
    out_file = f"{job_id}_compilation.mp4"
    out_path = os.path.join(OUTPUTS_DIR, out_file)
    list_txt = os.path.join(OUTPUTS_DIR, f"{job_id}_concat.txt")
    
    clips_sorted = sorted(clips, key=lambda c: float(c.get("start_time", 0)))
    with open(list_txt, "w", encoding="utf-8") as f:
        for c in clips_sorted:
            vpath = os.path.join(OUTPUTS_DIR, c["video_file"])
            if os.path.exists(vpath):
                f.write(f"file '{os.path.abspath(vpath).replace(chr(92), '/')}'\n")
    
    cmd = [FFMPEG_EXE, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if os.path.exists(list_txt):
        try: os.remove(list_txt)
        except OSError: pass

    if r.returncode != 0 or not os.path.exists(out_path):
        cmd_fallback = [
            FFMPEG_EXE, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_txt,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "160k", out_path
        ]
        subprocess.run(cmd_fallback, capture_output=True, text=True)

    if os.path.exists(out_path):
        update_job(job_id, compilation_video_url=f"/video/{out_file}")
        return out_file
    raise RuntimeError("Failed to generate compilation video.")
