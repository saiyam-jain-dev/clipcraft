import os
import json
import requests
import google.oauth2.credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from urllib.parse import urlencode

# Scopes needed for YouTube upload and channel metadata
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

TOKEN_PATHS = [
    "/app/data/youtube_token.json",
    os.path.join(os.path.dirname(__file__), "youtube_token.json"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "youtube_token.json"),
]

SECRETS_PATHS = [
    "/app/data/youtube_client_secrets.json",
    os.path.join(os.path.dirname(__file__), "youtube_client_secrets.json"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "youtube_client_secrets.json"),
]


def get_client_credentials() -> dict:
    """Returns {'client_id': ..., 'client_secret': ...} or None."""
    # 1. Direct environment variables (recommended for Railway)
    cid = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    csecret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if cid and csecret:
        return {"client_id": cid, "client_secret": csecret}

    # 2. Raw JSON in env var
    raw_json = os.environ.get("YOUTUBE_CLIENT_SECRETS_JSON", "").strip()
    if raw_json:
        try:
            data = json.loads(raw_json)
            parsed = data.get("web") or data.get("installed") or data
            if parsed.get("client_id") and parsed.get("client_secret"):
                return {"client_id": parsed["client_id"], "client_secret": parsed["client_secret"]}
        except Exception:
            pass

    # 3. File on disk
    for path in SECRETS_PATHS:
        if os.path.isfile(path) and os.path.getsize(path) > 10:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                parsed = data.get("web") or data.get("installed") or data
                if parsed.get("client_id") and parsed.get("client_secret"):
                    return {"client_id": parsed["client_id"], "client_secret": parsed["client_secret"]}
            except Exception:
                pass

    return None


def save_client_credentials(client_id: str, client_secret: str) -> str:
    """Saves Client ID and Secret to disk for future authentications."""
    target_file = "/app/data/youtube_client_secrets.json" if os.path.isdir("/app/data") else SECRETS_PATHS[1]
    os.makedirs(os.path.dirname(target_file), exist_ok=True)
    payload = {
        "web": {
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return target_file


def get_youtube_token_file() -> str:
    for path in TOKEN_PATHS:
        if os.path.isfile(path) and os.path.getsize(path) > 10:
            return path
    if os.path.isdir("/app/data"):
        return "/app/data/youtube_token.json"
    return TOKEN_PATHS[1]


def get_youtube_service():
    """Builds and returns an authenticated YouTube Data API v3 service, automatically refreshing tokens."""
    token_file = get_youtube_token_file()
    token_data = None
    if os.path.isfile(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                token_data = json.load(f)
        except Exception:
            token_data = None

    if not token_data:
        token_env = os.environ.get("YOUTUBE_TOKEN_JSON", "").strip()
        if token_env:
            try:
                token_data = json.loads(token_env)
            except Exception:
                token_data = None

    if not token_data:
        return None

    try:
        credentials = google.oauth2.credentials.Credentials.from_authorized_user_info(token_data, scopes=SCOPES)
        creds_info = get_client_credentials() or {}
        client_id = creds_info.get("client_id") or token_data.get("client_id")
        client_secret = creds_info.get("client_secret") or token_data.get("client_secret")

        if credentials.expired and credentials.refresh_token:
            if client_id and client_secret:
                credentials.client_id = client_id
                credentials.client_secret = client_secret
            credentials.refresh(GoogleRequest())
            # Save refreshed token
            try:
                os.makedirs(os.path.dirname(token_file), exist_ok=True)
                with open(token_file, "w", encoding="utf-8") as f:
                    f.write(credentials.to_json())
            except Exception:
                pass

        return build("youtube", "v3", credentials=credentials)
    except Exception as e:
        print(f"[YOUTUBE_AUTH_ERROR] Failed to initialize YouTube service: {e}", flush=True)
        return None


def get_auth_url(redirect_uri: str) -> str:
    """Generates the Google OAuth 2.0 consent URL for YouTube upload permissions."""
    creds = get_client_credentials()
    if not creds:
        raise RuntimeError("YouTube OAuth credentials not configured. Please provide Client ID and Client Secret.")
    
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """Exchanges an authorization code for access and refresh tokens, saving them to disk."""
    creds = get_client_credentials()
    if not creds:
        raise RuntimeError("YouTube OAuth credentials not configured.")
    
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code.strip(),
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    resp = requests.post(token_url, data=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Google authorization failed ({resp.status_code}): {resp.text}")

    token_data = resp.json()
    token_data["client_id"] = client_id
    token_data["client_secret"] = client_secret

    token_file = get_youtube_token_file()
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    return token_data


def disconnect_youtube():
    """Removes stored tokens to disconnect the YouTube channel."""
    token_file = get_youtube_token_file()
    if os.path.isfile(token_file):
        try:
            os.remove(token_file)
        except OSError:
            pass


def get_channel_profile():
    """Returns basic profile information about the connected YouTube channel."""
    service = get_youtube_service()
    if not service:
        return None
    try:
        res = service.channels().list(mine=True, part="snippet,statistics").execute()
        items = res.get("items", [])
        if items:
            ch = items[0]
            snippet = ch.get("snippet", {})
            stats = ch.get("statistics", {})
            return {
                "id": ch.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description", "")[:200],
                "custom_url": snippet.get("customUrl"),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "subscriber_count": stats.get("subscriberCount"),
                "video_count": stats.get("videoCount"),
            }
    except Exception as e:
        print(f"[YOUTUBE PROFILE ERROR] {e}", flush=True)
    return None


def upload_to_youtube(video_path: str, title: str, description: str, tags: list = None, privacy: str = "private"):
    """Uploads a video to YouTube with resumable multi-part upload and returns the video ID and URL."""
    youtube = get_youtube_service()
    if not youtube:
        raise RuntimeError("YouTube not connected. Please connect your YouTube channel first.")

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found at: {video_path}")

    # Optimize title and description for YouTube Shorts
    clean_title = (title or "Viral Clip").strip()
    if "#Shorts" not in clean_title and "#shorts" not in clean_title:
        clean_title = f"{clean_title[:85]} #Shorts"

    clean_desc = (description or "").strip()
    if "#Shorts" not in clean_desc and "#shorts" not in clean_desc:
        clean_desc = f"{clean_desc}\n\n#Shorts #viral #clips"

    tag_list = [t.replace("#", "").strip() for t in (tags or ["Shorts", "viral", "clipcraft"]) if t]

    body = {
        "snippet": {
            "title": clean_title[:100],
            "description": clean_desc[:5000],
            "tags": tag_list[:15],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": privacy if privacy in ("public", "unlisted", "private") else "private",
            "selfDeclaredMadeForKids": False,
        }
    }

    insert_request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(video_path, chunksize=1024 * 1024 * 4, resumable=True)
    )

    response = None
    while response is None:
        status, response = insert_request.next_chunk()
        if status:
            print(f"[YOUTUBE UPLOAD] Progress: {int(status.progress() * 100)}%", flush=True)

    video_id = response.get("id")
    return {
        "id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}" if video_id else None,
        "response": response
    }
