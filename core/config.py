import os
import json
import shutil
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

FFMPEG_EXE = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
NODE_EXE = shutil.which("node") or shutil.which("nodejs") or (r"C:\Program Files\nodejs\node.exe" if os.name == "nt" else "/usr/bin/node")
YUNET_MODEL_PATH = os.path.join(CORE_DIR, "face_detection_yunet.onnx")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

OUT_W, OUT_H = 1080, 1920
DEFAULT_BRAND_NAME = "ImSamxyy"

DEFAULT_SETTINGS = {
    "brand_name": DEFAULT_BRAND_NAME,
    # Fast hosted model via Groq's OpenAI-compatible API. Add a GROQ_API_KEY
    # environment variable or paste the key in Studio Settings.
    "llm_provider": "openai_compatible",
    "gemini_api_key": "",
    "gemini_model": "gemini-3.5-flash-lite",
    # Any OpenAI-compatible endpoint: Groq, OpenRouter, Ollama, LM Studio, OpenAI
    "openai_base_url": "https://api.groq.com/openai/v1",
    "openai_api_key": "",
    "openai_model": "openai/gpt-oss-120b",
    # JSON list of extra OpenAI-compatible engines tried after the two above, in order:
    # [{"name": "...", "base_url": "https://.../v1", "model": "...", "api_key": ""}]. Edit in settings.json.
    "fallback_providers": "",
    "whisper_model": "small",
    # e.g. "chrome", "edge", "firefox" when YouTube asks to "confirm you're not a bot"
    "cookies_from_browser": "",
    # Path to Netscape format cookies.txt file for yt-dlp
    "ytdlp_cookies_file": "",
    # Oxylabs residential proxy for keyless engines (optional)
    "proxy_host": "",
    "proxy_port": "",
    "proxy_user": "",
    "proxy_pass": "",
    # Optional Kaggle / Remote Dual-GPU Worker (e.g. Cloudflare tunnel URL https://...trycloudflare.com)
    "remote_worker_url": "",
    # "true" disables local GPU fallback when the user wants to keep this machine idle.
    "remote_render_required": "false",
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
    if "COOKIES_FROM_BROWSER" in os.environ:
        data["cookies_from_browser"] = os.environ.get("COOKIES_FROM_BROWSER", "")
    if "YTDLP_COOKIES_FILE" in os.environ:
        data["ytdlp_cookies_file"] = os.environ.get("YTDLP_COOKIES_FILE", "")
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
