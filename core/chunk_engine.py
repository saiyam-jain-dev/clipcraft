import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .config import DOWNLOADS_DIR, OUTPUTS_DIR, load_settings
from .jobs import update_job, log_event, replace_clip
from .renderer import render_clip
from . import remote_source as remote


def get_hardware_profile() -> dict:
    """Detects system hardware: GPU count, VRAM, system RAM, and RAM-disk (/dev/shm).
    Specifically tuned for Kaggle 30GB RAM + Dual Tesla T4 GPUs (cuda:0 & cuda:1).
    """
    gpus = []
    # 1. Try PyTorch if available
    try:
        import torch
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(i)
                gpus.append({
                    "id": i,
                    "name": props.name,
                    "memory_mb": round(props.total_memory / (1024 * 1024), 1)
                })
    except Exception:
        pass

    # 2. Try nvidia-smi if torch gave nothing or not installed
    if not gpus:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        gpus.append({
                            "id": int(parts[0]),
                            "name": parts[1],
                            "memory_mb": float(parts[2])
                        })
        except Exception:
            pass

    # Detect RAM & RAM-disk (/dev/shm in Linux / Kaggle)
    ram_gb = 0.0
    try:
        if os.name == "nt":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
        else:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        ram_gb = round(kb / (1024 * 1024), 1)
                        break
    except Exception:
        ram_gb = 8.0

    # Kaggle environment flag
    is_kaggle = os.path.exists("/kaggle") or "KAGGLE_KERNEL_RUN_TYPE" in os.environ

    # High-speed RAM disk (only on machines with >= 6GB RAM so low-memory containers do not OOM)
    ram_disk = None
    if os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK) and ram_gb >= 6.0:
        try:
            usage = shutil.disk_usage("/dev/shm")
            if usage.free > 1024 * 1024 * 1024:  # At least 1GB free
                ram_disk = "/dev/shm/clipcraft_ram"
                os.makedirs(ram_disk, exist_ok=True)
        except Exception:
            ram_disk = None

    gpu_count = len(gpus)
    # On Dual T4 GPUs (Kaggle), each GPU handles 3 parallel NVENC streams = 6 parallel workers
    if gpu_count >= 2:
        max_workers = min(6, gpu_count * 3)
    elif gpu_count == 1:
        max_workers = 3
    else:
        # On CPU (e.g. Railway or basic cloud VMs):
        # Run 1 worker sequentially for maximum stability, zero socket hangs, and fast throughput.
        max_workers = 1

    return {
        "gpu_count": gpu_count,
        "gpus": gpus,
        "ram_total_gb": ram_gb,
        "ram_disk_path": ram_disk,
        "is_kaggle": is_kaggle,
        "max_workers": max_workers
    }


def render_clips_chunked_parallel(
    job_id: str,
    source: str,
    clips: list,
    words: list,
    options: dict,
    base_progress: float = 60.0,
    progress_callback=None
) -> list:
    """Orchestrates parallel chunking across Dual GPUs and 30GB RAM.
    
    1. Distributes clips across available GPUs (GPU 0 & GPU 1 in round-robin).
    2. Downloads stream chunks directly into RAM disk (/dev/shm) when available.
    3. Concurrently encodes 9:16 layout using hardware NVENC.
    4. Aggregates progress and safely cleans up in-memory chunk buffers.
    """
    hw = get_hardware_profile()
    num_gpus = hw["gpu_count"]
    scratch_dir = hw["ram_disk_path"] or DOWNLOADS_DIR
    max_workers = min(hw["max_workers"], len(clips))

    if num_gpus >= 2:
        gpu_info = f"⚡ Dual-GPU Parallel Engine ({hw['gpus'][0]['name']} & {hw['gpus'][1]['name']}) with {max_workers} workers"
    elif num_gpus == 1:
        gpu_info = f"⚡ Hardware NVENC Engine ({hw['gpus'][0]['name']}) with {max_workers} workers"
    else:
        gpu_info = f"Multi-core CPU Engine with {max_workers} workers"

    if hw["ram_disk_path"]:
        gpu_info += " [RAM-disk /dev/shm zero-I/O active]"

    log_event(job_id, gpu_info)
    update_job(job_id, status_message=f"Starting parallel render: {gpu_info}")

    video_path = source if os.path.isfile(source) else None
    if video_path:
        src_info = None
    else:
        try:
            src_info = remote.fetch_info(source, format=remote.VIDEO_FORMAT)
        except Exception:
            try:
                src_info = remote.fetch_info(source, format="best/b")
            except Exception:
                src_info = remote.fetch_info(source)

    total = len(clips)
    done_count = [0]
    clip_progress = {c["clip_id"]: 0.0 for c in clips}
    progress_lock = threading.Lock()
    rendered_results = []
    errors = []

    def update_overall(status_override=None):
        with progress_lock:
            avg_pct = sum(clip_progress.values()) / max(1, total)
            overall = round(base_progress + (99.0 - base_progress) * (avg_pct / 100.0), 1)
            msg = status_override or f"Rendering ({done_count[0]}/{total} ready) · {gpu_info}"
            update_job(job_id, progress_pct=overall, status_message=msg)
            if progress_callback:
                progress_callback(overall, msg)

    def process_one_clip(index: int, clip: dict):
        cid = clip["clip_id"]
        # Shard across GPUs: even to GPU 0, odd to GPU 1
        target_gpu = (index % num_gpus) if num_gpus > 0 else None
        
        with progress_lock:
            clip_progress[cid] = 15.0
        update_overall(f"Cutting clip {index + 1}/{total}: \"{clip.get('title', '')[:35]}\"...")

        def on_clip_prog(p, detail=None):
            with progress_lock:
                clip_progress[cid] = min(100.0, 15.0 + float(p) * 0.85)
            update_overall(f"Encoding clip {index + 1}/{total} ({int(p)}%) · \"{clip.get('title', '')[:30]}\"")

        # Step 1: Ingest chunk (directly to RAM disk if available)
        chunk_file = None
        try:
            log_event(job_id, f"Cutting clip {index + 1}/{total}: \"{clip.get('title', '')[:35]}\"...")
            if video_path:
                in_video = video_path
                in_clip = clip
                in_words = words
            else:
                s0 = max(0.0, clip["start_time"] - 1.0)
                s1 = clip["end_time"] + 1.0
                chunk_file = os.path.join(scratch_dir, f"chunk_{job_id}_{cid}.mp4")
                
                # Fetch section
                remote.download_section(source, s0, s1, chunk_file, info=src_info, gpu_id=target_gpu)
                
                in_video = chunk_file
                in_clip = {
                    **clip,
                    "id": cid,
                    "start_time": clip["start_time"] - s0,
                    "end_time": clip["end_time"] - s0
                }
                in_words = [
                    {**w, "start": round(w["start"] - s0, 2), "end": round(w["end"] - s0, 2)}
                    for w in words if s0 <= w["start"] and w["end"] <= s1
                ]

            # Step 2: Render clip with hardware NVENC targeting target_gpu
            rendered = render_clip(
                in_video,
                in_clip,
                in_words,
                job_id,
                options,
                progress_callback=on_clip_prog,
                gpu_id=target_gpu
            )

            # Update clip data in job
            updated = dict(clip)
            updated.update(rendered)
            updated.pop("preview", None)
            updated.pop("preview_file", None)
            replace_clip(job_id, cid, updated)

            with progress_lock:
                done_count[0] += 1
                clip_progress[cid] = 100.0
            update_overall()
            
            target_str = f"GPU {target_gpu}" if target_gpu is not None else "CPU"
            log_event(job_id, f"Clip ready ({done_count[0]}/{total}) [{target_str}]: \"{clip.get('title', '')[:40]}\"")
            return updated

        except Exception as ex:
            log_event(job_id, f"Clip failed ({cid}): {str(ex)[:200]}", "error")
            with progress_lock:
                clip_progress[cid] = 100.0
            update_overall()
            raise ex
        finally:
            # Immediate clean-up of RAM chunk to keep memory lean
            if chunk_file and os.path.exists(chunk_file):
                try:
                    os.remove(chunk_file)
                except OSError:
                    pass

    # Launch parallel worker pool across dual GPUs
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process_one_clip, i, c): c for i, c in enumerate(clips)}
        for fut in as_completed(futures):
            clip = futures[fut]
            try:
                res = fut.result()
                rendered_results.append(res)
            except Exception as e:
                errors.append(f"{clip.get('title', '')[:30]}: {str(e)[:150]}")

    if errors and not rendered_results:
        log_event(job_id, f"Parallel batch render encountered issues ({errors[0]}). Clips are ready for preview and on-demand render.", "warn")

    return rendered_results
