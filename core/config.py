import os
import json
import threading
import imageio_ffmpeg

# httpx (used by huggingface_hub to fetch Whisper models) crashes on IPv6 entries like "::1" / "[::1]" in NO_PROXY.
for _var in ("NO_PROXY", "no_proxy"):
    if os.environ.get(_var):
        os.environ[_var] = ",".join(p for p in os.environ[_var].split(",") if "::" not in p)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(BASE_DIR, "core")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")
JOBS_DIR = os.path.join(BASE_DIR, "jobs")

for d in [UPLOADS_DIR, DOWNLOADS_DIR, OUTPUTS_DIR, JOBS_DIR]:
    os.makedirs(d, exist_ok=True)

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
NODE_EXE = r"C:\Program Files\nodejs\node.exe"
YUNET_MODEL_PATH = os.path.join(CORE_DIR, "face_detection_yunet.onnx")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

OUT_W, OUT_H = 1080, 1920
DEFAULT_BRAND_NAME = "ImSamxyy"

DEFAULT_SETTINGS = {
    "brand_name": DEFAULT_BRAND_NAME,
    # "gemini" | "openai_compatible" | "none"
    "llm_provider": "gemini",
    "gemini_api_key": "",
    "gemini_model": "gemini-3.5-flash-lite",
    # Any OpenAI-compatible endpoint: Groq, OpenRouter, Ollama, LM Studio, OpenAI
    "openai_base_url": "https://api.groq.com/openai/v1",
    "openai_api_key": "",
    "openai_model": "llama-3.3-70b-versatile",
    # JSON list of extra OpenAI-compatible engines tried after the two above, in order:
    # [{"name": "...", "base_url": "https://.../v1", "model": "...", "api_key": ""}]. Edit in settings.json.
    "fallback_providers": "",
    "whisper_model": "small",
    # e.g. "chrome", "edge", "firefox" when YouTube asks to "confirm you're not a bot"
    "cookies_from_browser": "",
    # Oxylabs residential proxy for keyless engines (optional)
    "proxy_host": "",
    "proxy_port": "",
    "proxy_user": "",
    "proxy_pass": "",
}
SECRET_KEYS = ("gemini_api_key", "openai_api_key", "proxy_pass")

_settings_lock = threading.RLock()


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    with _settings_lock:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
            except (OSError, ValueError):
                pass
    if not data["gemini_api_key"]:
        data["gemini_api_key"] = os.getenv("GEMINI_API_KEY", "")
    if not data["openai_api_key"]:
        data["openai_api_key"] = os.getenv("OPENAI_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
    return data


def save_settings(updates: dict) -> dict:
    with _settings_lock:
        current = load_settings()
        for k, v in updates.items():
            if k not in DEFAULT_SETTINGS or v is None:
                continue
            current[k] = v.strip() if isinstance(v, str) else v
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    return current


def public_settings() -> dict:
    """Settings safe to send to the browser: secrets replaced by a 'set' flag."""
    s = load_settings()
    for k in SECRET_KEYS:
        s[k + "_set"] = bool(s[k])
        s[k] = ""
    s["fallback_providers"] = ""  # holds API keys; never sent to the browser
    s["proxy_pass"] = ""  # never send to the browser
    return s
