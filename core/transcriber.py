import os
import re
import json
import hashlib
import importlib.util
import threading

_MODEL_CACHE = {}
_model_lock = threading.Lock()
_dll_handles = []


def _add_cuda_dll_dirs():
    # ctranslate2 needs cuBLAS/cuDNN DLLs on Windows; the CUDA build of torch ships them.
    if os.name != "nt":
        return
    spec = importlib.util.find_spec("torch")
    if spec and spec.origin:
        lib = os.path.join(os.path.dirname(spec.origin), "lib")
        if os.path.isdir(lib):
            _dll_handles.append(os.add_dll_directory(lib))
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dll_dirs()

from faster_whisper import WhisperModel  # noqa: E402
import ctranslate2  # noqa: E402


def _load_model(size: str, device: str):
    key = (size, device)
    with _model_lock:
        if key not in _MODEL_CACHE:
            compute = "int8_float16" if device == "cuda" else "int8"
            print(f">> [Whisper] loading '{size}' on {device} ({compute})")
            _MODEL_CACHE[key] = WhisperModel(size, device=device, compute_type=compute)
        return _MODEL_CACHE[key]


# Steers Whisper toward Hindi+English speech; the Devanagari output is then
# transliterated to casual Hinglish (Roman letters) word-by-word.
HINGLISH_PROMPT = ("यह हिंदी और अंग्रेज़ी में गेमिंग वीडियो है। GTA, गेमप्ले, क्लिपिंग, वीडियो, टूल। "
                   "Aaj hum gaming ke baare mein baat karenge.")


def _run(model, audio_path, language, progress_callback, initial_prompt=None):
    whisper_language = "hi" if language == "hinglish" else language
    prompt = initial_prompt or (HINGLISH_PROMPT if language == "hinglish" else None)
    segments_gen, info = model.transcribe(
        audio_path,
        language=whisper_language or None,
        initial_prompt=prompt,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
    )
    words, segments = [], []
    total = info.duration or 1
    for seg in segments_gen:
        seg_words = []
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                seg_words.append({"word": text, "start": round(w.start, 2), "end": round(w.end, 2)})
        words.extend(seg_words)
        segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()})
        if progress_callback:
            progress_callback(min(100.0, seg.end / total * 100))
    if language == "hinglish":
        from .translit import to_hinglish
        for w in words:
            w["word"] = to_hinglish(w["word"])
    return {"language": info.language, "duration": info.duration, "segments": segments, "words": words}


def transcribe(audio_path: str, model_size: str = "small", language: str = None,
               offset: float = 0.0, progress_callback=None, cache_key: str = None,
               initial_prompt: str = None) -> dict:
    """Word-level transcript. Timestamps are shifted by `offset` so they match the source video."""
    cache_path = None
    if cache_key:
        h = hashlib.md5(f"{cache_key}|{model_size}|{language}|{offset}".encode()).hexdigest()[:16]
        cache_path = os.path.join(os.path.dirname(audio_path), f"transcript_{h}.json")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    try:
        result = _run(_load_model(model_size, device), audio_path, language, progress_callback, initial_prompt)
    except RuntimeError as e:
        if device != "cuda":
            raise
        print(f">> [Whisper] GPU failed ({e}); retrying on CPU")
        result = _run(_load_model(model_size, "cpu"), audio_path, language, progress_callback, initial_prompt)

    if offset:
        for w in result["words"]:
            w["start"] = round(w["start"] + offset, 2)
            w["end"] = round(w["end"] + offset, 2)
        for s in result["segments"]:
            s["start"] = round(s["start"] + offset, 2)
            s["end"] = round(s["end"] + offset, 2)

    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
    return result


_TERMINAL = re.compile(r'[.!?…]+[\'")\]]*$')


def build_sentences(words: list, max_words: int = 30, gap_break: float = 0.9) -> list:
    """Groups words into sentences using punctuation and pauses. Each sentence keeps word index bounds."""
    sentences, cur = [], []

    def flush(terminal):
        if cur:
            sentences.append({
                "idx": len(sentences),
                "start": words[cur[0]]["start"],
                "end": words[cur[-1]]["end"],
                "w0": cur[0],
                "w1": cur[-1],
                "text": " ".join(words[i]["word"] for i in cur),
                "terminal": terminal,
            })
            cur.clear()

    for i, w in enumerate(words):
        if cur and w["start"] - words[cur[-1]]["end"] >= gap_break:
            flush(True)
        cur.append(i)
        if _TERMINAL.search(w["word"]):
            flush(True)
        elif len(cur) >= max_words:
            flush(False)
    flush(True)
    return sentences
