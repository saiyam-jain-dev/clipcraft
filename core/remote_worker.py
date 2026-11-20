import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .config import OUTPUTS_DIR
from .jobs import update_job, log_event, replace_clip

_worker_probe_cache = {}
_worker_probe_lock = threading.Lock()
_worker_probe_ttl = 20


def _worker_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def test_remote_worker(base_url: str) -> dict:
    """Check whether the configured cloud worker is reachable and current."""
    if not base_url or not str(base_url).strip():
        return {"ok": False, "error": "No remote worker URL provided."}
    url = _worker_url(base_url)

    with _worker_probe_lock:
        cached = _worker_probe_cache.get(url)
        if cached and time.monotonic() - cached[0] < _worker_probe_ttl:
            return dict(cached[1])
    try:
        response = requests.get(f"{url}/health", timeout=(5, 8))
        if response.status_code != 200:
            return {"ok": False, "error": f"Remote worker returned HTTP {response.status_code}"}
        data = response.json()
        capabilities = data.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            return {"ok": False, "error": "Remote worker returned invalid capabilities."}
        result = {
            "ok": True,
            "url": url,
            "gpu_count": data.get("gpu_count", 0),
            "gpus": data.get("gpus", []),
            "ram_total_gb": data.get("ram_total_gb", 0),
            "is_kaggle": data.get("is_kaggle", False),
            "capabilities": capabilities,
            "supports_clipcraft_render": "clipcraft-render-v2" in capabilities,
            "engine": data.get("engine", "Remote GPU Engine"),
            "message": f"Connected: {data.get('engine', 'GPU Worker')} ({data.get('gpu_count', 0)} GPUs, {data.get('ram_total_gb', 0)}GB RAM)",
        }
        with _worker_probe_lock:
            _worker_probe_cache[url] = (time.monotonic(), result)
        return dict(result)
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "Timed out connecting to the remote GPU worker. Start the Kaggle notebook and check its tunnel."}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Cannot reach the remote GPU worker: {str(exc)[:120]}"}
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Remote worker returned an invalid health response: {str(exc)[:100]}"}


def _download_artifact(url, job_id, filename, min_bytes=1):
    if not filename:
        return None
    if os.path.basename(filename) != filename or any(part in filename for part in ("/", "\\", "..")):
        raise RuntimeError("Remote worker returned an invalid output filename.")
    local_path = os.path.join(OUTPUTS_DIR, filename)
    if os.path.isfile(local_path) and os.path.getsize(local_path) >= min_bytes:
        return local_path
    part_path = local_path + ".part"
    try:
        with requests.get(f"{url}/api/clips/{job_id}/{filename}", stream=True, timeout=(8, 60)) as response:
            response.raise_for_status()
            with open(part_path, "wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
        if not os.path.isfile(part_path) or os.path.getsize(part_path) < min_bytes:
            raise RuntimeError(f"Remote output is incomplete: {filename}")
        os.replace(part_path, local_path)
        return local_path
    finally:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def offload_render_to_remote(
    worker_url: str,
    job_id: str,
    source: str,
    clips: list,
    words: list,
    options: dict,
    base_progress: float = 60.0,
) -> list:
    """Send rendering to cloud and publish each finished clip locally as soon as it arrives."""
    url = _worker_url(worker_url)
    if not url:
        raise RuntimeError("No cloud GPU worker URL was provided.")
    log_event(job_id, f"Offloading {len(clips)} clip renders to the cloud GPU worker.")
    update_job(job_id, status_message="Cloud GPU is rendering your clips...")
    payload = {"job_id": job_id, "source": source, "clips": clips, "words": words, "options": options}
    response = requests.post(f"{url}/api/render_batch", json=payload, timeout=(8, 30))
    if response.status_code not in (200, 202):
        raise RuntimeError(f"Cloud worker rejected the render job (HTTP {response.status_code}).")

    # Give a 10-clip cloud batch enough room while keeping the end-to-end target visible.
    # The caller measures end-to-end time. Keep the cloud request alive long
    # enough for a 10-clip batch to finish or yield incremental playable clips.
    deadline = time.monotonic() + max(120, min(165, 45 + len(clips) * 12))
    poll_interval = 1.0
    downloaded = set()
    updated_clips = []
    originals = {str(c.get("clip_id") or c.get("id")): c for c in clips}
    last_log_count = 0

    def download_one(remote_clip):
        clip_id = str(remote_clip.get("clip_id") or remote_clip.get("id") or "")
        original = originals.get(clip_id)
        if not original:
            raise RuntimeError(f"Cloud worker returned an unknown clip id: {clip_id}")
        video_file = remote_clip.get("video_file")
        if not video_file:
            raise RuntimeError(f"Cloud worker did not return video for {clip_id}.")
        _download_artifact(url, job_id, video_file, min_bytes=50_000)
        updated = dict(original)
        updated.update({
            "video_file": video_file,
            "video_url": f"/video/{video_file}",
            "layout": remote_clip.get("layout") or "fit",
        })
        thumb_file = remote_clip.get("thumb_file")
        if thumb_file:
            _download_artifact(url, job_id, thumb_file, min_bytes=1)
            updated["thumb_url"] = f"/media/{thumb_file}"
        subtitle_file = remote_clip.get("subtitle_file")
        if subtitle_file:
            _download_artifact(url, job_id, subtitle_file, min_bytes=1)
            updated["subtitle_url"] = f"/download/{subtitle_file}"
        updated.pop("preview", None)
        preview_file = updated.pop("preview_file", None)
        replace_clip(job_id, clip_id, updated)
        if preview_file:
            try:
                os.remove(os.path.join(OUTPUTS_DIR, os.path.basename(preview_file)))
            except OSError:
                pass
        return clip_id, updated

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            status_response = requests.get(f"{url}/api/jobs/{job_id}/status", timeout=(5, 10))
            if status_response.status_code != 200:
                continue
            data = status_response.json()
        except (requests.RequestException, ValueError):
            continue

        remote_logs = data.get("logs", [])
        for entry in remote_logs[last_log_count:]:
            log_event(job_id, f"[Cloud GPU] {entry.get('msg', '')}", entry.get("level", "info"))
        last_log_count = max(last_log_count, len(remote_logs))
        progress = float(data.get("progress_pct", 0.0) or 0.0)
        overall = round(base_progress + (99.0 - base_progress) * progress / 100.0, 1)
        update_job(job_id, progress_pct=overall,
                   status_message=data.get("status_message", "Rendering on the cloud GPU..."))

        ready = []
        for remote_clip in data.get("clips", []):
            clip_id = str(remote_clip.get("clip_id") or remote_clip.get("id") or "")
            if clip_id and clip_id not in downloaded:
                ready.append(remote_clip)
        if ready:
            with ThreadPoolExecutor(max_workers=min(4, len(ready))) as pool:
                futures = [pool.submit(download_one, clip) for clip in ready]
                for future in as_completed(futures):
                    clip_id, updated = future.result()
                    downloaded.add(clip_id)
                    updated_clips.append(updated)
                    log_event(job_id, f"Playable cloud clip ready ({len(downloaded)}/{len(clips)}): {updated.get('title', '')[:60]}")

        if data.get("status") == "completed":
            if len(downloaded) != len(clips):
                raise RuntimeError(f"Cloud worker completed with {len(downloaded)}/{len(clips)} clips.")
            log_event(job_id, f"All {len(updated_clips)} cloud clips downloaded and playable.")
            return updated_clips
        if data.get("status") == "failed":
            detail = data.get("error") or data.get("status_message") or "Cloud render failed."
            errors = data.get("errors") or []
            if errors:
                detail += " " + "; ".join(str(item) for item in errors[:2])
            raise RuntimeError(f"Cloud render failed after {len(downloaded)} clips were made playable: {detail}")

    raise RuntimeError(f"Cloud render did not finish in time; {len(downloaded)}/{len(clips)} completed clips were saved.")
