import os
import json
import time
import hashlib
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from .config import DOWNLOADS_DIR, OUTPUTS_DIR, load_settings
from .jobs import update_job, append_clip, get_job, replace_clip, log_event, enqueue
from .downloader import download_video, extract_audio, format_duration, grab_thumbnail
from .transcriber import transcribe, build_sentences
from .virality_engine import audio_energy_profile, find_viral_moments, raised_voice_profile
from .motion import motion_profile
from . import learn
from . import action as action_cues
from .renderer import render_clip
from . import remote_source as remote

FULL_WHISPER_LIMIT = 40 * 60  # longer caption-less videos only transcribe their loudest sections


def _is_local(source: str) -> bool:
    return os.path.isfile(source)


def _words_path(job_id: str) -> str:
    return os.path.join(DOWNLOADS_DIR, f"words_{job_id}.json")


def _range(duration: float, options: dict):
    start, end = options.get("range_start", 0), options.get("range_end", 0)
    if start >= duration:
        raise ValueError("Start time is past the end of the video.")
    if end and end > duration + 1:
        raise ValueError("End time is past the end of the video.")
    if end and end <= start:
        raise ValueError("End time must be after start time.")
    effective_end = min(end, duration) if end else duration
    if effective_end - start < 8:
        raise ValueError("Choose at least 8 seconds of video to analyze.")
    return start, effective_end


def _transcript(video_path, options, settings, progress_callback=None, on_start=None):
    """Cached audio extract + Whisper transcript for the job's analysed range."""
    start, end = options.get("range_start", 0), options.get("range_end", 0)
    cache_key = f"{os.path.basename(video_path)}|{start:g}|{end:g}"
    audio_id = hashlib.md5(cache_key.encode()).hexdigest()[:16]
    audio_path = os.path.join(DOWNLOADS_DIR, f"audio_{audio_id}.wav")
    if not os.path.exists(audio_path):
        extract_audio(video_path, audio_path, start, end)
    if on_start:
        on_start()
    result = transcribe(audio_path, settings["whisper_model"], language=options.get("language") or None,
                        offset=start, cache_key=cache_key, progress_callback=progress_callback)
    return audio_path, result


def _analyze_local(job_id, source, options, settings, progress):
    """Uploaded file: fully transcribe short videos, sample long ones efficiently."""
    meta = download_video(source, progress_callback=progress("download", 3, 22, "Preparing video..."))
    video_path, duration = meta["video_path"], meta["duration"]
    start, end = _range(duration, options)
    resolution = f"{meta.get('width')}x{meta.get('height')}" if meta.get("width") else ""
    update_job(job_id, video_title=meta["title"], video_thumbnail=meta.get("thumbnail", ""),
               video_duration=duration, video_resolution=resolution, stage="transcribe", progress_pct=24,
               status_message=f"Uploaded video: {resolution}, {format_duration(duration)}. Extracting speech audio...")
    span = end - start
    if span > FULL_WHISPER_LIMIT:
        # Full Whisper transcription scales with runtime and can take hours on CPU.
        # Scan cheap audio loudness first, then spend Whisper only on promising windows.
        cache_key = f"{os.path.basename(video_path)}|{start:g}|{end:g}"
        audio_id = hashlib.md5(cache_key.encode()).hexdigest()[:16]
        audio_path = os.path.join(DOWNLOADS_DIR, f"audio_{audio_id}.wav")
        update_job(job_id, status_message=f"Long video ({format_duration(span)}): scanning audio and sampling the strongest sections...")
        if not os.path.exists(audio_path):
            extract_audio(video_path, audio_path, start, end)
        energy = audio_energy_profile(audio_path, offset=start)
        windows = remote.loud_windows(
            energy,
            count=min(12, max(6, options.get("target_clips", 5) * 2)),
            length=max(150.0, options.get("max_duration", 60) * 2.5),
        )
        log_event(job_id, f"Long upload: sampling {len(windows)} high-energy sections ({sum(b-a for a,b in windows)/60:.0f} minutes) instead of transcribing the full {span/60:.0f} minutes.")
        words, language = [], options.get("language") or None
        for index, (ws, we) in enumerate(windows):
            part_path = os.path.join(DOWNLOADS_DIR, f"audio_{job_id}_{index}.wav")
            if not os.path.exists(part_path):
                extract_audio(video_path, part_path, ws, we)
            update_job(job_id, progress_pct=round(25 + 24 * index / max(1, len(windows)), 1),
                       status_message=f"Finding moments in section {index + 1}/{len(windows)} of this long video...")
            part = transcribe(part_path, settings["whisper_model"], language=language, offset=ws,
                              cache_key=f"{os.path.basename(video_path)}|{ws:g}|{we:g}")
            words.extend(part["words"])
            language = language or part["language"]
        raised = None
        language = language or "en"
    else:
        audio_path, result = _transcript(
            video_path, options, settings,
            progress_callback=progress("transcribe", 25, 49, "Transcribing speech and aligning words..."),
            on_start=lambda: update_job(job_id, status_message=f"Transcribing with Whisper {settings['whisper_model']} (first use downloads the model)..."))
        energy = audio_energy_profile(audio_path, offset=start)
        raised = raised_voice_profile(audio_path, offset=start)
        words, language = result["words"], result["language"]
    return {"video_path": video_path, "words": words, "language": language,
            "energy": energy, "raised": raised, "start": start, "end": end}


def _analyze_remote(job_id, source, options, settings, progress):
    """Link: transcript from YouTube captions (seconds, nothing downloaded); audio-only fallback."""
    info = remote.fetch_info(source)
    duration = info.get("duration") or 0
    if not duration:
        raise RuntimeError("This link has no fixed length (still live?), so it cannot be clipped yet.")
    title = info.get("title") or "Untitled video"
    uploader = info.get("uploader") or info.get("channel") or ""
    by = f" by {uploader}" if uploader else ""
    update_job(job_id, video_title=title, video_thumbnail=info.get("thumbnail") or "", video_uploader=uploader,
               video_duration=duration, progress_pct=5, status_message=f"Found \"{title}\"{by} ({format_duration(duration)})")
    start, end = _range(duration, options)
    span = end - start
    update_job(job_id, stage="transcribe", progress_pct=8, status_message="Looking for YouTube captions...")
    result, energy = None, None
    try:
        result = remote.caption_transcript(info, start, end)
    except Exception as e:
        log_event(job_id, f"Could not read captions: {str(e)[:200]}", "warn")
    if result:
        lang, words = result["language"], result["words"]
        if options.get("language") == "hinglish" and str(lang).startswith("hi"):
            from .translit import hinglish_words
            words = hinglish_words(words)
            lang = "hinglish"
        log_event(job_id, f"Using YouTube {result['kind']} captions: {len(words)} words with timings. "
                          f"No video download needed for analysis.")
        return {"video_path": None, "words": words, "language": lang,
                "energy": None, "raised": None, "start": start, "end": end}

    log_event(job_id, "This video has no captions, so only its audio is downloaded for transcription.", "warn")
    audio = remote.download_audio(info, source, start, end,
                                  progress_callback=progress("transcribe", 10, 30, "Downloading audio only..."))
    log_event(job_id, f"Audio ready: {os.path.getsize(audio) / 1048576:.0f} MB")
    model = settings["whisper_model"]
    language = options.get("language") or None
    raised = None
    if span <= FULL_WHISPER_LIMIT:
        wav = os.path.splitext(audio)[0] + ".wav"
        if not os.path.exists(wav):
            extract_audio(audio, wav)
        update_job(job_id, progress_pct=30, status_message=f"Transcribing with Whisper {model}...")
        result = transcribe(wav, model, language=language, offset=start, cache_key=os.path.basename(wav),
                            progress_callback=progress("transcribe", 30, 49, "Transcribing speech and aligning words..."))
        words, lang = result["words"], result["language"]
        energy = audio_energy_profile(wav, offset=start)
        raised = raised_voice_profile(wav, offset=start)
    else:
        update_job(job_id, progress_pct=30, status_message="Measuring loudness across the whole video...")
        energy = remote.loudness_profile(audio, offset=start)
        max_dur = options.get("max_duration", 60)
        windows = remote.loud_windows(energy, count=min(12, max(6, options.get("target_clips", 5) * 2)),
                                      length=max(150.0, max_dur * 2.5))
        log_event(job_id, f"Long video without captions: transcribing the {len(windows)} loudest sections "
                          f"({sum(e - s for s, e in windows) / 60:.0f} of {span / 60:.0f} minutes)")
        words, lang = [], None
        for k, (ws, we) in enumerate(windows):
            wav = f"{os.path.splitext(audio)[0]}_{int(ws)}.wav"
            if not os.path.exists(wav):
                extract_audio(audio, wav, ws - start, we - start)
            update_job(job_id, progress_pct=round(30 + 19 * k / len(windows), 1),
                       status_message=f"Transcribing section {k + 1}/{len(windows)} "
                                      f"({format_duration(ws)} to {format_duration(we)})...")
            part = transcribe(wav, model, language=language, offset=ws, cache_key=os.path.basename(wav))
            words += part["words"]
            lang = lang or part["language"]
    return {"video_path": None, "words": words,
            "language": "hinglish" if language == "hinglish" else (lang or "en"),
            "energy": energy, "raised": raised, "start": start, "end": end}


def _render(job_id, source, video_path, clip, words, options, callback, src_info=None):
    """Render from the local file, or download just this clip's seconds from the link first."""
    if video_path:
        return render_clip(video_path, clip, words, job_id, options, progress_callback=callback)
    s0, s1 = max(0.0, clip["start_time"] - 1.0), clip["end_time"] + 1.0
    section = os.path.join(DOWNLOADS_DIR, f"sec_{job_id}_{clip['id']}.mp4")
    log_event(job_id, f"Downloading only {format_duration(s0)} to {format_duration(s1)} of the video...")
    started = time.monotonic()
    remote.download_section(source, s0, s1, section, info=src_info)
    log_event(job_id, f"Clip footage ready: {os.path.getsize(section) / 1048576:.1f} MB in {time.monotonic() - started:.0f} s")
    shifted = [{**w, "start": round(w["start"] - s0, 2), "end": round(w["end"] - s0, 2)}
               for w in words if s0 <= w["start"] and w["end"] <= s1]
    try:
        return render_clip(section, {**clip, "start_time": clip["start_time"] - s0, "end_time": clip["end_time"] - s0},
                           shifted, job_id, options, progress_callback=callback)
    finally:
        try:
            os.remove(section)
        except OSError:
            pass


def rerender_clip(job_id: str, clip_id: str, start: float, end: float, overrides: dict = None):
    """Re-render one clip of a finished job with new start/end times and optional
    layout/caption overrides (the editor's trim + restyle)."""
    job = get_job(job_id)
    options = {**job["options"], **(overrides or {})}
    try:
        settings = load_settings()
        if str(settings.get("remote_render_required", "false")).lower() in ("1", "true", "yes"):
            raise RuntimeError("Cloud-only rendering is enabled. Re-editing a completed clip needs the cloud re-edit worker, so the existing clip was kept and this laptop was not used.")
        clip = next(c for c in job["clips"] if c["clip_id"] == clip_id)
        update_job(job_id, status="processing", stage="render", progress_pct=5,
                   status_message="Preparing the trimmed clip...")
        source = job["source"]
        if not _is_local(source) and os.path.exists(_words_path(job_id)):
            video_path = None
            with open(_words_path(job_id), encoding="utf-8") as f:
                words = json.load(f)
        else:  # uploads, and link projects made before clip-only downloads (full video is cached)
            video_path = download_video(source)["video_path"]
            words = _transcript(video_path, options, load_settings())[1]["words"]
        rev = int(clip.get("trim_rev", 0)) + 1
        message = f"Re-rendering: {clip.get('title', '')[:65]}"

        def callback(pct, detail=None):
            update_job(job_id, progress_pct=round(10 + 0.89 * pct, 1), status_message=message)

        rendered = _render(job_id, source, video_path,
                           {**clip, "id": f"{clip_id}_t{rev}", "start_time": start, "end_time": end},
                           words, options, callback)
        old_file = clip.get("video_file", "")
        updated = dict(clip)
        updated.update(rendered)
        updated.update(start_time=start, end_time=end, duration=round(end - start, 1), trim_rev=rev,
                       transcript=" ".join(w["word"] for w in words if start <= w["start"] and w["end"] <= end + 0.05))
        replace_clip(job_id, clip_id, updated)
        if old_file and old_file != rendered["video_file"]:
            stem = os.path.splitext(old_file)[0]
            for ext in (".mp4", ".jpg", ".ass", ".srt"):
                try:
                    os.remove(os.path.join(OUTPUTS_DIR, stem + ext))
                except OSError:
                    pass
        update_job(job_id, status="completed", stage="completed", progress_pct=100,
                   status_message="Trimmed clip is ready.")
    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="completed", stage="completed", progress_pct=100,
                   warning=f"Trim failed, the previous version of the clip was kept ({str(e)[:200]})",
                   status_message="Trim failed. The previous version of the clip was kept.")


def _preview_frames(job_id, source, video_path, clips, src_info):
    """One thumbnail per moment. Grabs a single frame — remote links are read via HTTP range
    seeks and local files are seeked directly, so no clip footage is downloaded."""
    out = {}

    def grab(clip):
        name = f"{job_id}_{clip['id']}_prev.jpg"
        path = os.path.join(OUTPUTS_DIR, name)
        mid = (clip["start_time"] + clip["end_time"]) / 2
        try:
            if video_path:
                grab_thumbnail(video_path, mid, path)
            elif src_info:
                try:
                    remote.preview_frame(src_info, mid, path, source=source)
                except Exception as frame_err:
                    # Fallback to remote thumbnail if range-seek was blocked
                    thumb_url = src_info.get("thumbnail") or (f"https://i.ytimg.com/vi/{src_info.get('id')}/hqdefault.jpg" if src_info.get("id") else None)
                    if thumb_url:
                        import requests
                        r = requests.get(thumb_url, timeout=5)
                        if r.status_code == 200:
                            with open(path, "wb") as f:
                                f.write(r.content)
                    if not os.path.exists(path):
                        raise frame_err
            else:
                return None
        except Exception as e:
            log_event(job_id, f"No preview frame for \"{clip.get('title', '')[:40]}\": {str(e)[:120]}", "warn")
            return None
        return clip["id"], name

    with ThreadPoolExecutor(max_workers=min(4, len(clips))) as pool:
        for item in pool.map(grab, clips):
            if item:
                out[item[0]] = item[1]
    return out


def _clip_words(job_id, source, options, clip):
    """Transcript words for one clip: the job's cached word list, or a fresh caption pull."""
    path = _words_path(job_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if _is_local(source):
        return _transcript(source, options, load_settings())[1]["words"]
    info = remote.fetch_info(source)
    res = remote.caption_transcript(info, max(0.0, clip["start_time"] - 5), clip["end_time"] + 5)
    if res:
        return res["words"]
    raise RuntimeError("This project's transcript is no longer cached. Create a new project to render it.")


def _finish_render(job_id, clip_id, clip, rendered):
    updated = dict(clip)
    updated.update(rendered)
    updated.pop("preview", None)
    updated.pop("preview_file", None)
    prev = clip.get("preview_file")
    replace_clip(job_id, clip_id, updated)
    if prev:
        try:
            os.remove(os.path.join(OUTPUTS_DIR, prev))
        except OSError:
            pass
    return updated


def render_pending_clip(job_id: str, clip_id: str):
    """Render one preview clip on demand — the only step that downloads clip footage."""
    job = get_job(job_id)
    options = job["options"]
    try:
        settings = load_settings()
        if str(settings.get("remote_render_required", "false")).lower() in ("1", "true", "yes"):
            raise RuntimeError("Cloud-only rendering is enabled. Start the current cloud worker and run the project again; this laptop was not used.")
        clip = next(c for c in job["clips"] if c.get("clip_id") == clip_id)
        if clip.get("video_file"):
            update_job(job_id, status="completed", stage="completed", progress_pct=100,
                       status_message="This clip is already rendered.")
            return
        update_job(job_id, status="processing", stage="render", progress_pct=5,
                   status_message=f"Rendering: {clip.get('title', '')[:65]}")
        source = job["source"]
        words = _clip_words(job_id, source, options, clip)
        video_path = source if _is_local(source) else None
        src_info = None if video_path else remote.fetch_info(source, format=remote.VIDEO_FORMAT)

        def callback(pct, detail=None):
            update_job(job_id, progress_pct=round(10 + 0.89 * pct, 1),
                       status_message=f"Rendering: {clip.get('title', '')[:65]}")

        rendered = _render(job_id, source, video_path, {**clip, "id": clip_id}, words, options,
                           callback, src_info)
        _finish_render(job_id, clip_id, clip, rendered)
        log_event(job_id, f"Clip rendered on demand: {rendered.get('layout', '')} layout")
        update_job(job_id, status="completed", stage="completed", progress_pct=100,
                   status_message="Clip is ready to download.")
    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="completed", stage="completed", progress_pct=100,
                   warning=f"Render failed, the preview was kept ({str(e)[:200]})",
                   status_message="Render failed. The preview was kept; try again.")


def render_all_pending(job_id: str, base_progress: float = 10.0):
    """Render every clip that only has a preview in parallel, using either the Kaggle
    Dual-GPU remote worker (if configured) or the local multi-worker GPU chunk engine."""
    job = get_job(job_id)
    options = job["options"]
    source = job["source"]
    pending = [c for c in job.get("clips", []) if not c.get("video_file")]
    if not pending:
        update_job(job_id, status="completed", stage="completed", progress_pct=100,
                   status_message="All clips are already rendered.")
        return
    try:
        words = _clip_words(job_id, source, options, pending[0])
        settings = load_settings()
        worker_url = settings.get("remote_worker_url", "").strip()
        cloud_required = str(settings.get("remote_render_required", "false")).lower() in ("1", "true", "yes")

        if cloud_required and not worker_url:
            raise RuntimeError("Cloud rendering is required, but no Remote Worker URL is configured. Start the Kaggle worker and add its tunnel URL in Settings.")

        # Path 1: Remote Kaggle Dual-GPU Worker (if configured)
        if worker_url:
            try:
                from .remote_worker import offload_render_to_remote, test_remote_worker
                remote_state = test_remote_worker(worker_url)
                if not remote_state.get("ok"):
                    raise RuntimeError(remote_state.get("error", "Cloud GPU worker is offline."))
                required_caps = {"clipcraft-render-v2", "word-captions", "incremental-results", "clip-metadata-merge"}
                missing_caps = required_caps.difference(remote_state.get("capabilities", []))
                if missing_caps:
                    raise RuntimeError("Cloud GPU worker is outdated. Restart it with the current Kaggle notebook to enable captioned rendering.")
                if os.path.isfile(source):
                    raise RuntimeError("Cloud-only rendering currently accepts YouTube links; local video files are not available on Kaggle.")
                rendered_clips = offload_render_to_remote(
                    worker_url, job_id, source, pending, words, options, base_progress=base_progress
                )
                # A full compilation is optional; generating it locally can
                # re-encode video and violate the user's cloud-only choice.
                if not cloud_required:
                    try:
                        from .jobs import generate_compilation_video
                        generate_compilation_video(job_id)
                    except Exception:
                        pass
                update_job(job_id, status="completed", stage="completed", progress_pct=100,
                           status_message=f"All {len(rendered_clips)} clips rendered and ready on the cloud GPU!",
                           completed_at=time.time())
                return
            except Exception as e:
                if cloud_required:
                    raise RuntimeError(f"Cloud render stopped safely; this laptop was not used for rendering. {str(e)[:250]}") from e
                log_event(job_id, f"Remote cloud worker unavailable ({str(e)[:150]}); using the local renderer.", "warn")

        # Path 2: Parallel Dual/Single GPU Chunk Engine
        from .chunk_engine import render_clips_chunked_parallel
        rendered_clips = render_clips_chunked_parallel(
            job_id=job_id,
            source=source,
            clips=pending,
            words=words,
            options=options,
            base_progress=base_progress
        )
        try:
            from .jobs import generate_compilation_video
            if rendered_clips and len(rendered_clips) > 0:
                generate_compilation_video(job_id)
        except Exception:
            pass

        ready_count = len(rendered_clips) if rendered_clips else 0
        if ready_count == len(pending):
            status_msg = f"All {ready_count} clips rendered and ready!"
        elif ready_count > 0:
            status_msg = f"{ready_count}/{len(pending)} clips rendered! You can preview and render remaining clips on demand."
        else:
            status_msg = "Moments identified! You can preview any clip instantly or render on demand."

        update_job(job_id, status="completed", stage="completed", progress_pct=100,
                   status_message=status_msg,
                   completed_at=time.time())

    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="failed", error=str(e), status_message=str(e), completed_at=time.time())


def _profile_feature(profile, start, end, scale=2.5):
    """Mean z-score of a per-second profile over [start, end), squashed to 0..1."""
    if not profile or not len(profile.get("z", [])):
        return None
    z, hop, off = profile["z"], profile["hop"], profile["offset"]
    a = max(0, int((start - off) / hop))
    b = min(len(z), int((end - off) / hop))
    if b <= a:
        return None
    return float(np.clip(z[a:b].mean() / scale, 0.0, 1.0))


def _clip_features(clip, src, motion):
    feats = learn.clip_features(clip)
    s, e = clip["start_time"], clip["end_time"]
    for key, profile in (("energy", src.get("energy")), ("motion", motion), ("shout", src.get("raised"))):
        v = _profile_feature(profile, s, e)
        if v is not None:
            feats[key] = v
    return feats


def _score_clips(job_id, source, src, motion, clips, options, mode):
    """Attach 0..1 features to every clip (used by the feedback loop). In deep mode
    also measure on-screen action — motion, flashes, police-light strobes, cuts —
    in the best candidates and re-rank with the learned weights."""
    weights = learn.load_weights()
    video_path = src["video_path"]
    src_info = None
    measured = {}
    if mode == "deep":
        update_job(job_id, status_message="Deep mode: checking on-screen action in the best moments...")
        if video_path:
            def measure(clip):
                try:
                    return clip["id"], action_cues.analyze(video_path, clip["start_time"],
                                                           clip["end_time"] - clip["start_time"])
                except Exception:
                    return clip["id"], None
        else:
            try:
                src_info = remote.fetch_info(source)
            except Exception:
                src_info = None

            def measure(clip):
                tmp = None
                try:
                    if not src_info:
                        return clip["id"], None
                    sec_start = max(0.0, clip["start_time"] - 0.5)
                    tmp = os.path.join(DOWNLOADS_DIR, f"act_{job_id}_{clip['id']}")
                    path = remote.download_lowres_section(source, sec_start, clip["end_time"] + 0.5,
                                                          tmp, info=src_info)
                    return clip["id"], action_cues.analyze(path, clip["start_time"] - sec_start,
                                                           clip["end_time"] - clip["start_time"])
                except Exception as e:
                    log_event(job_id, f"Action check skipped for one moment: {str(e)[:120]}", "warn")
                    return clip["id"], None
                finally:
                    if tmp:
                        for p in (tmp, tmp + ".mp4", tmp + ".mkv", tmp + ".webm"):
                            try:
                                os.remove(p)
                            except OSError:
                                pass
        with ThreadPoolExecutor(max_workers=min(4, len(clips))) as ex:
            for cid, act in ex.map(measure, clips):
                measured[cid] = act
    for clip in clips:
        feats = _clip_features(clip, src, motion)
        act = measured.get(clip["id"])
        if act:
            for k in ("motion", "flash", "strobe", "cuts"):
                if act.get(k) is not None:
                    feats[k] = max(feats.get(k) or 0.0, float(act[k]))
        learned = learn.score(feats, weights)
        clip["features"] = {k: round(float(v), 3) for k, v in feats.items()}
        clip["learned_score"] = round(learned * 100)
        base = float(clip.get("virality_score") or 50)
        clip["virality_score"] = int(max(1, min(99, round(0.72 * base + 28 * learned))))
    clips.sort(key=lambda c: c["virality_score"], reverse=True)
    return clips


def run_clipcraft_pipeline(job_id: str, source: str, options: dict):
    settings = load_settings()
    last_update = [0.0]

    def progress(stage, low, high, message):
        def callback(pct, detail=None):
            if time.monotonic() - last_update[0] < 0.7:
                return
            last_update[0] = time.monotonic()
            update_job(job_id, progress_pct=round(low + (high - low) * pct / 100, 1),
                       stage=stage, status_message=detail or message)
        return callback

    try:
        update_job(job_id, status="processing", stage="download", progress_pct=2,
                   status_message="Reading video details...")
        analyze = _analyze_local if _is_local(source) else _analyze_remote
        src = analyze(job_id, source, options, settings, progress)
        words, start, end = src["words"], src["start"], src["end"]
        effective_duration = end - start
        max_dur = min(options["max_duration"], effective_duration)
        min_dur = min(options["min_duration"], max_dur)
        with open(_words_path(job_id), "w", encoding="utf-8") as f:
            json.dump(words, f)
        sentences = build_sentences(words)
        log_event(job_id, f"Transcript: {len(words)} words, {len(sentences)} sentences, language {src['language']}")
        update_job(job_id, detected_language=src["language"], stage="analyze", progress_pct=51,
                   transcript_words=len(words), status_message="Finding reactions, jokes, arguments, and stories worth posting...")
        mode = options.get("mode", "fast")
        motion = None
        sparse_speech = len(words) / max(1e-6, effective_duration / 60) < 25
        if src["video_path"] and (mode == "deep" or options.get("focus") == "highlights" or sparse_speech):
            update_job(job_id, status_message="Reading cuts, action, and camera movement...")
            span = max(1.0, end - start)
            motion = motion_profile(src["video_path"], start, end,
                                    report=lambda s: update_job(
                                        job_id, progress_pct=round(51 + 8 * min(1.0, s / span), 1),
                                        status_message=f"Scanning movement and cuts... "
                                                       f"{int(min(s, span))}/{int(span)}s"))
        analysis = find_viral_moments(sentences, words, src["energy"], settings, min_dur=min_dur, max_dur=max_dur,
                                      target=options["target_clips"], prompt=options.get("prompt", ""),
                                      duration=effective_duration, range_start=start, motion=motion,
                                      focus=options.get("focus", "auto"), raised_voice=src.get("raised"),
                                      log=lambda msg, level="info": log_event(job_id, msg, level, status=level == "info"))
        clips = analysis["clips"]
        if not clips:
            raise RuntimeError("No suitable moments found. Try a longer range or a different clip duration.")
        log_event(job_id, f"Moment picker ({analysis['engine']}) chose {len(clips)} clips")
        clips = _score_clips(job_id, source, src, motion, clips, options, mode)
        src_info = None
        if not src["video_path"]:
            try:
                src_info = remote.fetch_info(source, format=remote.VIDEO_FORMAT)
            except Exception as e:
                log_event(job_id, f"Could not cache stream info ({str(e)[:120]}); preview frames may be missing.", "warn")
        total = len(clips)
        update_job(job_id, stage="render", progress_pct=60, clips_count=total, engine=analysis["engine"],
                   warning=analysis["warning"],
                   status_message=f"Found {total} moments. Grabbing preview frames (no footage downloaded)...")
        thumbs = _preview_frames(job_id, source, src["video_path"], clips, src_info)
        for clip in clips:
            data = {k: v for k, v in clip.items() if k != "id"}
            data["clip_id"] = clip["id"]
            if clip["id"] in thumbs:
                data["thumb_url"] = f"/media/{thumbs[clip['id']]}"
                data["preview_file"] = thumbs[clip["id"]]
            data["preview"] = True
            append_clip(job_id, data)
        update_job(job_id, stage="render", progress_pct=60,
                   status_message=f"{total} moments found. Ready for instant preview or download...")
        try:
            render_all_pending(job_id, base_progress=60.0)
        except Exception as render_ex:
            log_event(job_id, f"Auto-render notice: {str(render_ex)[:120]}. Clips ready for instant preview or download.", "warn")
            update_job(job_id, status="completed", stage="completed", progress_pct=100,
                       status_message=f"{total} moments identified! Tap Play Preview or Download Video on any clip.",
                       completed_at=time.time())
    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="failed", error=str(e), status_message=str(e), completed_at=time.time())
